#!/usr/bin/env python3
"""Diburit TTS hook for Claude Code.

Stop-hook entry point. Runs after every assistant turn. Reads stdin (which
Claude Code populates with {session_id, ...}) and checks
~/Diburit/latest/metadata.json. If the metadata is fresh (< 10 min old)
and `pasted_at` is set, we know the previous user turn was a voice paste
from Diburit and should be spoken aloud.

Three tiers (per `feedback_sayit_tts_layering` memory):
  1. SHORT  (cleaned <= 220 chars) -> read whole thing
  2. PUNCHLINE (long w/ short trailing paragraph) -> read just that line
  3. COMPLEX (very long / tables / many code blocks) -> Groq Llama
     summarizes in 1-2 Hebrew sentences, read summary

The metadata file is consumed (replaced with a "consumed:true" marker)
once we have spoken, so the next typed user message does not trigger TTS.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

__version__ = "1.3.0"


DIBURIT_HOME = Path.home() / "Diburit"
LATEST_DIR = DIBURIT_HOME / "latest"
SETTINGS_FILE = DIBURIT_HOME / "settings.json"
METADATA_NAME = "metadata.json"
SPEECH_AIFF = Path("/tmp/diburit_tts.aiff")
SPEECH_MP3 = Path("/tmp/diburit_tts.mp3")
METADATA_MAX_AGE_SECONDS = 600

DEFAULT_VOICE = "Carmit"
DEFAULT_VOLUME = 0.8
# Playback speed multiplier. Applied via `afplay -r` with `-q 1` to keep
# pitch constant — gTTS in particular reads Hebrew very slowly, so users
# typically bump this above 1.0. Clamped to the same range Diburit's
# Speed submenu enforces (see SPEECH_RATE_LEVELS in diburit.py).
DEFAULT_SPEECH_RATE = 1.0
MIN_SPEECH_RATE = 0.5
MAX_SPEECH_RATE = 2.5

# `voice` values prefixed with EDGE_PREFIX are Microsoft Edge TTS neural
# voices (e.g. "edge:he-IL-AvriNeural"), rendered via the `edge-tts` Python
# package. `GTTS_PREFIX` selects the `gtts` (Google Translate TTS) backend
# (e.g. "gtts:iw"). Unprefixed values are macOS `say` voice names. The
# prefix is the discriminator used by `speak()` and the diburit.py voice
# preview to pick a render backend, so settings.json stays a single string
# field. Keep prefixes in sync with `diburit.EDGE_PREFIX` / `GTTS_PREFIX`.
EDGE_PREFIX = "edge:"
GTTS_PREFIX = "gtts:"
EDGE_RENDER_TIMEOUT_SEC = 20
GTTS_RENDER_TIMEOUT_SEC = 20

load_dotenv(DIBURIT_HOME / ".env")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_SUMMARIZER_MODEL = "llama-3.3-70b-versatile"
GROQ_SUMMARIZER_TIMEOUT_SEC = 15
GROQ_SUMMARIZER_INPUT_CHAR_LIMIT = 6000
GROQ_SUMMARIZER_MAX_TOKENS = 220

SHORT_THRESHOLD = 220
LONG_THRESHOLD = 1000
COMPLEX_CODE_FENCE_COUNT = 4
PUNCHLINE_MIN_LEN = 10
PUNCHLINE_MAX_LEN = 250
FALLBACK_SENTENCE_MAX_LEN = 300

# Short Hebrew placeholders that replace code so it isn't read aloud
# verbatim. Fenced ```...``` blocks become CODE_BLOCK_PLACEHOLDER; inline
# `...` longer than INLINE_CODE_MAX_PRESERVE becomes INLINE_CODE_PLACEHOLDER.
# Short inline code (variable names, file extensions) is unwrapped and read
# inline because it stays readable in speech.
CODE_BLOCK_PLACEHOLDER = "בלוק קוד"
INLINE_CODE_PLACEHOLDER = "סקריפט"
INLINE_CODE_MAX_PRESERVE = 20
_PLACEHOLDER_PHRASES = frozenset({CODE_BLOCK_PLACEHOLDER, INLINE_CODE_PLACEHOLDER})

SAY_RENDER_TIMEOUT_SEC = 20

# Claude Code can fire the Stop hook a few hundred milliseconds before the
# turn's final assistant message is flushed to the session JSONL. Without a
# wait we read the JSONL too early and grab the *previous* turn's assistant
# text, which then gets spoken in place of the current reply. Poll the
# JSONL for an assistant message that sits AFTER the latest user message
# before giving up.
JSONL_POLL_MAX_ATTEMPTS = 30
JSONL_POLL_INTERVAL_SEC = 0.1

# Observability log appended one JSONL row per Stop fire. Lets us verify in
# the wild that the after-user filter is picking the correct turn's reply
# (`assistant_after`) and not the stale "last assistant message anywhere in
# the file" (`assistant_global`) that the pre-fix code would have spoken.
DEBUG_LOG_FILE = DIBURIT_HOME / "tts_debug.log"
DEBUG_PREVIEW_CHARS = 120


def load_voice_settings() -> tuple:
    voice = DEFAULT_VOICE
    volume = DEFAULT_VOLUME
    rate = DEFAULT_SPEECH_RATE
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("voice"), str) and data["voice"].strip():
                voice = data["voice"].strip()
            try:
                volume = float(data.get("volume", DEFAULT_VOLUME))
            except (TypeError, ValueError):
                pass
            try:
                rate = float(data.get("speech_rate", DEFAULT_SPEECH_RATE))
            except (TypeError, ValueError):
                pass
        except Exception as exc:
            print(f"[tts] could not parse settings.json: {exc}", file=sys.stderr)
    return (
        voice,
        max(0.0, min(volume, 1.0)),
        max(MIN_SPEECH_RATE, min(rate, MAX_SPEECH_RATE)),
    )


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _preview(s: str) -> str:
    if not s:
        return ""
    s = s.strip().replace("\n", " ")
    return s if len(s) <= DEBUG_PREVIEW_CHARS else s[:DEBUG_PREVIEW_CHARS] + "…"


def _dbg_log(payload: dict) -> None:
    """Append one JSONL row to ~/Diburit/tts_debug.log. Best-effort — any
    I/O error is swallowed so a broken log path never breaks the hook."""
    try:
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()), **payload}
        with DEBUG_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_and_consume_metadata(latest_user_text: Optional[str]) -> Optional[dict]:
    """Read ~/Diburit/latest/metadata.json. If pasted_at is set and fresh,
    AND the Diburit transcript appears inside this session's latest user
    message, mark consumed and return the parsed payload. Otherwise return
    None and leave metadata as-is so the actual paste-target session can
    pick it up on its next Stop fire. The transcript-in-user-text check
    is what distinguishes the paste-target session from every other
    Claude Code window whose Stop hook also sees the same global metadata
    file — timestamp proximity alone is too loose (you can switch projects
    in seconds and type something there that's still within tolerance).
    Using `consumed` instead of `unlink` so the recordings/.../metadata.json
    file stays as a permanent history alongside its audio+transcript.

    The whole read-check-write is done while holding an exclusive `flock` on
    a sibling lockfile so two Stop-hook instances that fire back-to-back
    (e.g. user spams Enter on a long answer) cannot both observe the same
    pre-consumed metadata and double-speak."""
    if not LATEST_DIR.exists():
        return None
    metadata_path = LATEST_DIR / METADATA_NAME
    if not metadata_path.exists():
        return None

    # Lockfile lives next to the metadata so it follows the symlink target
    # and is naturally scoped per-utterance.
    lock_path = metadata_path.with_suffix(".json.lock")
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        print(f"[tts] could not open lockfile: {exc}", file=sys.stderr)
        return None

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError as exc:
            print(f"[tts] could not acquire lock: {exc}", file=sys.stderr)
            return None

        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        pasted_at = data.get("pasted_at")
        if not isinstance(pasted_at, (int, float)):
            return None
        if data.get("consumed"):
            return None
        if time.time() - pasted_at > METADATA_MAX_AGE_SECONDS:
            return None
        transcript = data.get("transcript")
        if (
            not isinstance(transcript, str)
            or not transcript.strip()
            or not latest_user_text
            or _normalize_ws(transcript) not in _normalize_ws(latest_user_text)
        ):
            return None
        data["consumed"] = True
        try:
            tmp = metadata_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, metadata_path)
        except OSError as exc:
            print(f"[tts] could not mark metadata consumed: {exc}", file=sys.stderr)
        return data
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def find_session_jsonl(session_id: str) -> Optional[Path]:
    matches = list((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def latest_user_and_assistant(jsonl: Path) -> tuple:
    """Return ``(user_text, assistant_after_user, assistant_global)``.

    - ``user_text``: text of the last user message in the JSONL.
    - ``assistant_after_user``: the last assistant text-block message whose
      JSONL line index is greater than the last user message's index — this
      is what we actually speak. ``""`` if Claude Code hasn't flushed the
      current turn's reply yet.
    - ``assistant_global``: the last assistant text-block message in the
      file regardless of position. We return it purely so the debug log
      can show when the after-user filter saved us from speaking a stale
      previous turn's reply (``assistant_global != assistant_after_user``)."""
    try:
        lines = jsonl.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ("", "", "")

    last_user_idx = -1
    last_user_text = ""
    assistants = []
    for idx, line in enumerate(lines):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = d.get("type")
        if t == "user":
            content = (d.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "\n".join(
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
                )
            else:
                continue
            if text.strip():
                last_user_idx = idx
                last_user_text = text
        elif t == "assistant":
            content = (d.get("message") or {}).get("content") or []
            parts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
            ]
            if parts:
                assistants.append((idx, "\n".join(parts).strip()))

    assistant_global = assistants[-1][1] if assistants else ""
    assistant_after_user = ""
    if last_user_idx != -1:
        for idx, text in assistants:
            if idx > last_user_idx:
                assistant_after_user = text

    return (last_user_text, assistant_after_user, assistant_global)


def strip_code_blocks(text: str) -> str:
    """Replace fenced code blocks and long inline code with short Hebrew
    placeholders so they aren't read aloud verbatim. Used both inside
    ``strip_markdown`` (so SHORT/PUNCHLINE paths see the placeholder, not
    the raw code) and as a pre-processing step before
    ``summarize_via_groq`` (so the LLM doesn't burn its budget on code
    listings the user wouldn't want narrated anyway)."""
    text = re.sub(r"```[\s\S]*?```", f" {CODE_BLOCK_PLACEHOLDER}. ", text)

    def _inline(m: re.Match) -> str:
        body = m.group(1)
        if len(body) > INLINE_CODE_MAX_PRESERVE:
            return f" {INLINE_CODE_PLACEHOLDER}. "
        return body
    return re.sub(r"`([^`\n]+)`", _inline, text)


def strip_markdown(text: str) -> str:
    text = strip_code_blocks(text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_#>]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text).strip()
    return text


def _is_just_placeholder(text: str) -> bool:
    """True if ``text`` is essentially just a code-block placeholder (after
    stripping whitespace/punctuation). Used to keep ``extract_punchline``
    from picking the placeholder when the original final paragraph was a
    code fence — speaking just 'בלוק קוד' gives the user no information."""
    stripped = re.sub(r"[\s.,!?]+", " ", text).strip()
    return stripped in _PLACEHOLDER_PHRASES


def looks_complex(raw: str) -> bool:
    if len(raw) > LONG_THRESHOLD:
        return True
    if re.search(r"^\s*\|.*\|\s*$", raw, re.MULTILINE):
        return True
    if raw.count("```") >= COMPLEX_CODE_FENCE_COUNT:
        return True
    return False


def extract_punchline(cleaned: str) -> Optional[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if len(paragraphs) >= 2:
        last = paragraphs[-1]
        if (
            PUNCHLINE_MIN_LEN <= len(last) <= PUNCHLINE_MAX_LEN
            and not _is_just_placeholder(last)
        ):
            return last
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    if len(sentences) >= 3:
        last = sentences[-1]
        if (
            PUNCHLINE_MIN_LEN <= len(last) <= PUNCHLINE_MAX_LEN
            and not _is_just_placeholder(last)
        ):
            return last
    return None


def summarize_via_groq(text: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return ""
    payload = {
        "model": GROQ_SUMMARIZER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "סכם בעברית במשפט אחד או שניים מה התשובה הבאה אומרת. "
                    "החזר רק את הסיכום עצמו, בלי הקדמה כמו 'הסיכום הוא'. "
                    "אם מופיעים בטקסט הפלייסהולדרים 'בלוק קוד' או 'סקריפט', "
                    "התייחס אליהם כאל קוד שהושמט — ציין שיש שם קוד אם זה רלוונטי, "
                    "אבל אל תנסה לתאר מה הקוד עושה."
                ),
            },
            {"role": "user", "content": text[:GROQ_SUMMARIZER_INPUT_CHAR_LIMIT]},
        ],
        "max_tokens": GROQ_SUMMARIZER_MAX_TOKENS,
        "temperature": 0.2,
    }
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=GROQ_SUMMARIZER_TIMEOUT_SEC,
        )
    except Exception as exc:
        print(f"[tts] groq request failed: {exc}", file=sys.stderr)
        return ""
    if resp.status_code != 200:
        print(f"[tts] groq HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return ""
    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        print(f"[tts] groq response malformed: {exc}", file=sys.stderr)
        return ""


def _render_edge_tts(text: str, edge_voice: str, out_path: Path) -> bool:
    """Render `text` with Microsoft Edge TTS into `out_path` (MP3).
    Returns True iff the file was written and has non-zero size. Any
    failure (missing package, network, voice not found) is logged and
    returns False so the caller can fall back to `say`."""
    try:
        import asyncio
        import edge_tts  # type: ignore
    except ImportError as exc:
        print(f"[tts] edge-tts not installed: {exc}", file=sys.stderr)
        return False

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, edge_voice)
        await communicate.save(str(out_path))

    try:
        if out_path.exists():
            out_path.unlink()
        asyncio.run(asyncio.wait_for(_run(), timeout=EDGE_RENDER_TIMEOUT_SEC))
    except Exception as exc:
        print(f"[tts] edge-tts render failed ({edge_voice}): {exc}", file=sys.stderr)
        return False
    try:
        ok = out_path.exists() and out_path.stat().st_size > 0
    except OSError:
        ok = False
    if not ok:
        print(f"[tts] edge-tts produced empty output for {edge_voice}", file=sys.stderr)
    return ok


def _render_gtts(text: str, gtts_lang: str, out_path: Path) -> bool:
    """Render `text` with gTTS (Google Translate TTS) into `out_path` (MP3).
    Returns True iff the file was written and has non-zero size. Any
    failure (missing package, network, rate limit) is logged and returns
    False so the caller can fall back to `say`."""
    try:
        from gtts import gTTS  # type: ignore
    except ImportError as exc:
        print(f"[tts] gtts not installed: {exc}", file=sys.stderr)
        return False
    try:
        if out_path.exists():
            out_path.unlink()
        gTTS(text=text, lang=gtts_lang).save(str(out_path))
    except Exception as exc:
        print(f"[tts] gtts render failed ({gtts_lang}): {exc}", file=sys.stderr)
        return False
    try:
        ok = out_path.exists() and out_path.stat().st_size > 0
    except OSError:
        ok = False
    if not ok:
        print(f"[tts] gtts produced empty output for {gtts_lang}", file=sys.stderr)
    return ok


def _afplay(path: Path, volume: float, rate: float = 1.0) -> None:
    # `-q 1` enables the high-quality time-stretch algorithm so that
    # rate != 1.0 preserves pitch instead of chipmunking the voice.
    r = max(MIN_SPEECH_RATE, min(rate, MAX_SPEECH_RATE))
    try:
        subprocess.Popen(
            ["afplay", "-v", f"{volume:.2f}", "-r", f"{r:.2f}", "-q", "1", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        print(f"[tts] afplay failed: {exc}", file=sys.stderr)


def _render_say(text: str, voice: str, env: dict) -> bool:
    try:
        subprocess.run(
            ["say", "-v", voice, "-o", str(SPEECH_AIFF), text],
            check=False, timeout=SAY_RENDER_TIMEOUT_SEC,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception as exc:
        print(f"[tts] say render failed: {exc}", file=sys.stderr)
        return False
    return SPEECH_AIFF.exists() and SPEECH_AIFF.stat().st_size > 0


def speak(text: str) -> None:
    if not text.strip():
        return
    voice, volume, rate = load_voice_settings()
    # `say` reads the text argv positionally as bytes. When the hook is
    # launched by Claude Code under launchd's stripped env (no LANG /
    # LC_CTYPE), the runtime treats argv as MacRoman, which mangles
    # Hebrew. Force a UTF-8 locale in the child env to be safe.
    env = os.environ.copy()
    env.setdefault("LC_CTYPE", "en_US.UTF-8")
    env.setdefault("LANG", "en_US.UTF-8")

    if voice.startswith(EDGE_PREFIX):
        edge_voice = voice[len(EDGE_PREFIX):]
        if _render_edge_tts(text, edge_voice, SPEECH_MP3):
            _afplay(SPEECH_MP3, volume, rate)
            return
        # Edge failed (offline, package missing, voice typo) - fall back
        # to Carmit so the user still hears the response.
        print(f"[tts] edge tts failed, falling back to {DEFAULT_VOICE}", file=sys.stderr)
        if _render_say(text, DEFAULT_VOICE, env):
            _afplay(SPEECH_AIFF, volume, rate)
        return

    if voice.startswith(GTTS_PREFIX):
        gtts_lang = voice[len(GTTS_PREFIX):]
        if _render_gtts(text, gtts_lang, SPEECH_MP3):
            _afplay(SPEECH_MP3, volume, rate)
            return
        # gTTS failed (offline, package missing, rate limit) - fall back
        # to Carmit so the user still hears the response.
        print(f"[tts] gtts failed, falling back to {DEFAULT_VOICE}", file=sys.stderr)
        if _render_say(text, DEFAULT_VOICE, env):
            _afplay(SPEECH_AIFF, volume, rate)
        return

    if _render_say(text, voice, env):
        _afplay(SPEECH_AIFF, volume, rate)


def choose_what_to_speak(raw: str) -> str:
    cleaned = strip_markdown(raw)
    if not cleaned:
        return ""
    if looks_complex(raw):
        # Strip code from the summarizer input too. ``looks_complex`` still
        # decides the path off the raw text (fence count is a useful
        # complexity signal), but Groq gets the placeholder-substituted
        # version so it doesn't summarize code listings verbatim.
        summary = summarize_via_groq(strip_code_blocks(raw))
        return summary or cleaned[:SHORT_THRESHOLD]
    if len(cleaned) <= SHORT_THRESHOLD:
        return cleaned
    punchline = extract_punchline(cleaned)
    if punchline:
        return punchline
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    return first_sentence[:FALLBACK_SENTENCE_MAX_LEN]


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        hook_input = {}

    session_id = hook_input.get("session_id")
    if not session_id:
        return

    jsonl = find_session_jsonl(session_id)
    if not jsonl:
        return

    user_text = ""
    raw = ""
    raw_global = ""
    attempts = 0
    for attempt in range(JSONL_POLL_MAX_ATTEMPTS):
        attempts = attempt + 1
        user_text, raw, raw_global = latest_user_and_assistant(jsonl)
        if raw:
            break
        time.sleep(JSONL_POLL_INTERVAL_SEC)

    # Consume metadata only after we have a current-turn assistant reply in
    # hand, so a failed poll leaves the metadata available for a later Stop
    # fire instead of silently swallowing the turn.
    metadata = read_and_consume_metadata(user_text) if (user_text and raw) else None
    speech = choose_what_to_speak(raw) if metadata else ""

    _dbg_log({
        "session": session_id[:8],
        "attempts": attempts,
        "transcript": _preview((metadata or {}).get("transcript", "")),
        "metadata_consumed": metadata is not None,
        "user_text": _preview(user_text),
        "assistant_after": _preview(raw),
        "assistant_global": _preview(raw_global),
        "diverged": bool(raw and raw_global and raw != raw_global),
        "speech": _preview(speech),
        "would_speak": bool(speech),
    })

    if speech:
        speak(speech)


if __name__ == "__main__":
    main()
