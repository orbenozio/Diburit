"""diburit_core — shared constants and pure functions for Diburit.

Imported by both diburit.py (macOS) and diburit_win.py (Windows).
No platform-specific imports — only stdlib + numpy/requests/soundfile/dotenv.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests
import soundfile as sf
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------

DIBURIT_HOME   = Path.home() / "Diburit"
RECORDINGS_DIR = DIBURIT_HOME / "recordings"
SETTINGS_FILE  = DIBURIT_HOME / "settings.json"

load_dotenv(DIBURIT_HOME / ".env")

# ---------------------------------------------------------------------------
# Audio constants
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16_000
CHANNELS    = 1
DTYPE       = "int16"

# ---------------------------------------------------------------------------
# Transcription — shared
# ---------------------------------------------------------------------------

TRANSCRIBE_LANGUAGE = "he"

# Hebrew vocabulary hint: keep English technical terms in Latin script instead
# of transliterating them. Used as the Groq `prompt` and the faster-whisper
# `initial_prompt` alike.
HEBREW_PROMPT = (
    "זה תמלול של דובר עברית שמשתמש לעיתים במונחים טכניים באנגלית. "
    "מילים כמו commit, git, terminal, install, function, class, repo, "
    "branch, pull request, debug, script, file, folder, server, build, "
    "deploy, log, hook, prompt, token, cache, queue, callback, README — "
    "השאר אותן באנגלית, לא בתעתיק עברי."
)

# Backwards-compat aliases (older code / tests referenced these names).
GROQ_PROMPT = HEBREW_PROMPT
GROQ_LANGUAGE = TRANSCRIBE_LANGUAGE

# Which engine turns audio into text.
#   "local" — faster-whisper running on this machine. No API key, no cost,
#             works offline. The default so a fresh install just works.
#   "groq"  — Groq cloud Whisper-large-v3. Fastest + best Hebrew, but each
#             user needs their own GROQ_API_KEY and pays per use.
BACKEND_LOCAL = "local"
BACKEND_GROQ  = "groq"
_BACKENDS     = frozenset({BACKEND_LOCAL, BACKEND_GROQ})
DEFAULT_BACKEND = BACKEND_LOCAL

# ---------------------------------------------------------------------------
# Groq backend
# ---------------------------------------------------------------------------

GROQ_URL     = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL   = "whisper-large-v3"
GROQ_TIMEOUT = 60

# ---------------------------------------------------------------------------
# Local backend (faster-whisper / CTranslate2)
# ---------------------------------------------------------------------------

# User-selectable local models. Key → (repo-or-name, human label).
# ivrit.ai models are Hebrew fine-tunes of Whisper, shipped in CTranslate2
# format so faster-whisper loads them directly. The plain Whisper sizes are
# multilingual fallbacks for users who want a smaller/lighter download.
LOCAL_MODELS: Dict[str, Tuple[str, str]] = {
    "ivrit-turbo": ("ivrit-ai/whisper-large-v3-turbo-ct2",
                    "ivrit.ai Turbo — Hebrew-tuned, fast (recommended)"),
    "ivrit-large": ("ivrit-ai/whisper-large-v3-ct2",
                    "ivrit.ai Large-v3 — Hebrew-tuned, most accurate (heavy)"),
    "whisper-large-v3": ("large-v3",
                    "Whisper large-v3 — multilingual, heavy"),
    "whisper-medium": ("medium",
                    "Whisper medium — multilingual, lighter"),
    "whisper-small": ("small",
                    "Whisper small — multilingual, fastest/least accurate"),
}
DEFAULT_LOCAL_MODEL = "ivrit-turbo"

# int8 keeps the model small in RAM and runs on CPU (most Windows users have
# no CUDA GPU). On a machine with a supported GPU, faster-whisper still works;
# this stays correct, just leaves speed on the table.
LOCAL_COMPUTE_TYPE = "int8"
LOCAL_DEVICE       = "cpu"

# WhisperModel load is expensive (reads weights off disk); cache per model key
# so we pay it once, not per utterance.
_local_model_cache: Dict[str, object] = {}


def local_model_label(model_key: str) -> str:
    entry = LOCAL_MODELS.get(model_key) or LOCAL_MODELS[DEFAULT_LOCAL_MODEL]
    return entry[1]


def _load_local_model(model_key: str):
    """Lazily import faster-whisper and load (and cache) the requested model.
    The import is deferred so machines that only ever use the Groq backend
    don't need faster-whisper installed at all."""
    cached = _local_model_cache.get(model_key)
    if cached is not None:
        return cached
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Local transcription needs faster-whisper. Install it with "
            "`pip install faster-whisper`, or switch the backend to Groq "
            "in settings."
        ) from exc
    repo = (LOCAL_MODELS.get(model_key) or LOCAL_MODELS[DEFAULT_LOCAL_MODEL])[0]
    # First load for a model downloads it from HuggingFace (cached under
    # ~/.cache/huggingface afterwards). This can take a while and needs net.
    model = WhisperModel(repo, device=LOCAL_DEVICE, compute_type=LOCAL_COMPUTE_TYPE)
    _local_model_cache[model_key] = model
    return model

# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------

SILENCE_PEAK_THRESHOLD     = 200
FOCUS_SETTLE_SEC           = 0.15
TRANSCRIBE_RETRY_BACKOFF   = (0.5, 1.0)
QUIT_TRANSCRIBE_GRACE_SEC  = 4.0
TRANSCRIPT_PREVIEW_CHARS   = 60
NOTIFICATION_PREVIEW_CHARS = 100
VOICE_LIST_TIMEOUT         = 4
PTT_MIN_HOLD_SEC           = 0.18

SILENCE_HALLUCINATIONS = frozenset({
    "תודה", "תודה רבה", "תודה רבה לכם", "תודה לכם", "שלום", "שלום שלום",
    "כן", "אוקיי", "בסדר", "להתראות",
    "thank you", "thanks", "bye", "you", "תרגום אבישי כהן",
})
_HALLUCINATION_NORMALIZE = re.compile(r"[\s.,!?\-,\"'״׳]+")

# ---------------------------------------------------------------------------
# Hotkey modes
# ---------------------------------------------------------------------------

HOTKEY_MODE_TOGGLE = "toggle"
HOTKEY_MODE_PTT    = "ptt"
_HOTKEY_MODES      = frozenset({HOTKEY_MODE_TOGGLE, HOTKEY_MODE_PTT})

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

MIN_SPEECH_RATE        = 0.5
MAX_SPEECH_RATE        = 2.5
MAX_RECORDINGS_PRESETS: List[int] = [25, 50, 100, 250, 500, 1000]

# How the transcript reaches the target app once transcribed.
PASTE_MODE_PASTE = "paste"  # instant Ctrl+V / Cmd+V of the whole text (default)
PASTE_MODE_TYPE  = "type"   # injected character-by-character (human-typing feel)
_PASTE_MODES = (PASTE_MODE_PASTE, PASTE_MODE_TYPE)
# Typing speed for PASTE_MODE_TYPE, in characters per second. The ceiling is
# deliberately conservative: SendInput injection starts dropping/duplicating
# characters past ~50 cps on Notepad (and heavier targets like Electron/VS Code
# have even less headroom), so 40 keeps a safety margin. The default is a brisk
# but reliable type-out; the floor is a slow, deliberate human pace.
MIN_TYPE_CPS     = 5.0
MAX_TYPE_CPS     = 40.0
DEFAULT_TYPE_CPS = 25.0

# Base settings shared by both platforms. Each platform adds its own `voice`.
_BASE_SETTINGS: Dict[str, object] = {
    "volume":                  0.8,
    "hotkey":                  "<cmd>+<shift>+m",
    "hotkey_mode":             HOTKEY_MODE_TOGGLE,
    "max_recordings_kept":     100,
    "speech_rate":             1.0,
    "show_status_rows":        True,
    "transcription_backend":   DEFAULT_BACKEND,
    "local_model":             DEFAULT_LOCAL_MODEL,
    "paste_mode":              PASTE_MODE_PASTE,
    "type_cps":                DEFAULT_TYPE_CPS,
}

# ---------------------------------------------------------------------------
# Voice lists
# ---------------------------------------------------------------------------

EDGE_PREFIX = "edge:"
GTTS_PREFIX = "gtts:"

EDGE_HEBREW_VOICES: List[Tuple[str, str]] = [
    ("Avri (Edge Neural)",         f"{EDGE_PREFIX}he-IL-AvriNeural"),
    ("Hila (Edge Neural)",         f"{EDGE_PREFIX}he-IL-HilaNeural"),
    ("Ava (Edge Multilingual)",    f"{EDGE_PREFIX}en-US-AvaMultilingualNeural"),
    ("Andrew (Edge Multilingual)", f"{EDGE_PREFIX}en-US-AndrewMultilingualNeural"),
]
GTTS_HEBREW_VOICES: List[Tuple[str, str]] = [
    ("Hebrew (gTTS)", f"{GTTS_PREFIX}iw"),
]


def edge_rate_str(rate: float) -> str:
    """Convert a speech-rate multiplier (1.0 = normal) into the percentage
    string edge-tts expects (e.g. 1.5 -> '+50%', 0.8 -> '-20%', 1.0 -> '+0%').
    edge-tts changes tempo at render time without shifting pitch, which is how
    Windows gets real readback-speed control (pygame can't re-time an MP3)."""
    r = max(MIN_SPEECH_RATE, min(rate, MAX_SPEECH_RATE))
    return f"{round((r - 1.0) * 100):+d}%"

# ---------------------------------------------------------------------------
# Atomic file write
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Settings load / save
# ---------------------------------------------------------------------------

def _load_settings(
    settings_file: Path = SETTINGS_FILE,
    defaults: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Read settings.json with per-key validation. Unknown/wrong-typed values
    fall back to defaults — a forward-compat shim so an old settings.json
    never bricks a newer Diburit. Pass `defaults` explicitly from the
    platform module so the correct voice default is used."""
    if defaults is None:
        defaults = _BASE_SETTINGS
    data: Dict[str, object] = dict(defaults)
    if not settings_file.exists():
        return data
    try:
        # utf-8-sig strips a leading BOM if present (PowerShell 5.1 writes one)
        on_disk = json.loads(settings_file.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[Diburit] settings.json unreadable: {exc}", file=sys.stderr)
        return data
    if not isinstance(on_disk, dict):
        return data
    if isinstance(on_disk.get("voice"), str) and on_disk["voice"].strip():
        data["voice"] = on_disk["voice"].strip()
    try:
        vol = float(on_disk.get("volume", defaults["volume"]))  # type: ignore
        data["volume"] = max(0.0, min(vol, 1.0))
    except (TypeError, ValueError):
        pass
    if isinstance(on_disk.get("hotkey"), str) and on_disk["hotkey"]:
        data["hotkey"] = on_disk["hotkey"]
    mode = on_disk.get("hotkey_mode")
    if isinstance(mode, str) and mode in _HOTKEY_MODES:
        data["hotkey_mode"] = mode
    try:
        n = int(on_disk.get("max_recordings_kept", defaults["max_recordings_kept"]))  # type: ignore
        data["max_recordings_kept"] = max(10, min(n, 10_000))
    except (TypeError, ValueError):
        pass
    try:
        rate = float(on_disk.get("speech_rate", defaults["speech_rate"]))  # type: ignore
        data["speech_rate"] = max(MIN_SPEECH_RATE, min(rate, MAX_SPEECH_RATE))
    except (TypeError, ValueError):
        pass
    if isinstance(on_disk.get("show_status_rows"), bool):
        data["show_status_rows"] = on_disk["show_status_rows"]
    backend = on_disk.get("transcription_backend")
    if isinstance(backend, str) and backend in _BACKENDS:
        data["transcription_backend"] = backend
    model = on_disk.get("local_model")
    if isinstance(model, str) and model in LOCAL_MODELS:
        data["local_model"] = model
    pmode = on_disk.get("paste_mode")
    if isinstance(pmode, str) and pmode in _PASTE_MODES:
        data["paste_mode"] = pmode
    try:
        cps = float(on_disk.get("type_cps", defaults["type_cps"]))  # type: ignore
        data["type_cps"] = max(MIN_TYPE_CPS, min(cps, MAX_TYPE_CPS))
    except (TypeError, ValueError):
        pass
    return data


def _save_settings(data: Dict[str, object], settings_file: Path = SETTINGS_FILE) -> None:
    try:
        _atomic_write(settings_file, json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        print(f"[Diburit] could not write settings.json: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Silence detection
# ---------------------------------------------------------------------------

def _audio_is_silent(data: np.ndarray, threshold: int = SILENCE_PEAK_THRESHOLD) -> bool:
    if data.size == 0:
        return True
    return int(np.max(np.abs(data))) < threshold


def _is_silence_hallucination(text: str) -> bool:
    normalized = _HALLUCINATION_NORMALIZE.sub(" ", text.lower()).strip()
    return normalized in SILENCE_HALLUCINATIONS or normalized == ""


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def _transcribe_with_groq(wav_path: Path) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set (check ~/Diburit/.env)")
    last_err: Optional[Exception] = None
    for attempt in range(len(TRANSCRIBE_RETRY_BACKOFF)):
        try:
            with open(wav_path, "rb") as fh:
                resp = requests.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (wav_path.name, fh, "audio/wav")},
                    data={
                        "model":           GROQ_MODEL,
                        "language":        GROQ_LANGUAGE,
                        "response_format": "text",
                        "prompt":          GROQ_PROMPT,
                    },
                    timeout=GROQ_TIMEOUT,
                )
        except Exception as exc:
            last_err = exc
            time.sleep(TRANSCRIBE_RETRY_BACKOFF[attempt])
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt + 1 < len(TRANSCRIBE_RETRY_BACKOFF):
            last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            time.sleep(TRANSCRIBE_RETRY_BACKOFF[attempt])
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.text.strip()
    raise last_err or RuntimeError("transcription failed")


def _transcribe_with_local(wav_path: Path, model_key: str = DEFAULT_LOCAL_MODEL) -> str:
    model = _load_local_model(model_key)
    segments, _info = model.transcribe(
        str(wav_path),
        language=TRANSCRIBE_LANGUAGE,
        initial_prompt=HEBREW_PROMPT,
        vad_filter=True,
    )
    return "".join(seg.text for seg in segments).strip()


def transcribe(
    wav_path: Path,
    backend: str = DEFAULT_BACKEND,
    local_model: str = DEFAULT_LOCAL_MODEL,
) -> str:
    """Turn a WAV file into Hebrew text using the configured backend.
    Single entry point both platform apps call — keeps the backend choice in
    one place instead of branching inside each `_transcribe_worker`."""
    if backend == BACKEND_GROQ:
        return _transcribe_with_groq(wav_path)
    return _transcribe_with_local(wav_path, local_model)


# ---------------------------------------------------------------------------
# Utterance / recordings
# ---------------------------------------------------------------------------

@dataclass
class Utterance:
    timestamp:       str
    dir:             Path
    audio_path:      Path
    transcript_path: Path
    metadata_path:   Path
    target_app:      Optional[str]     = None
    transcript:      Optional[str]     = None
    pasted_at:       Optional[float]   = None
    extra:           Dict[str, object] = field(default_factory=dict)

    @classmethod
    def fresh(cls, recordings_dir: Path = RECORDINGS_DIR) -> "Utterance":
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ts = f"{ts}_{datetime.now().microsecond:06d}"
        directory = recordings_dir / f"diburit_{ts}"
        directory.mkdir(parents=True, exist_ok=True)
        return cls(
            timestamp=ts,
            dir=directory,
            audio_path=directory / "audio.wav",
            transcript_path=directory / "transcript.txt",
            metadata_path=directory / "metadata.json",
        )

    def write_metadata(self) -> None:
        payload = {
            "schema_version": 1,
            "timestamp":  self.timestamp,
            "transcript": self.transcript,
            "target_app": self.target_app,
            "pasted_at":  self.pasted_at,
            "audio_path": str(self.audio_path),
            **self.extra,
        }
        _atomic_write(
            self.metadata_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )


def _repoint_latest(target_dir: Path, diburit_home: Path = DIBURIT_HOME) -> None:
    """Point ~/Diburit/latest at target_dir.

    Always writes latest.txt (a plain-text pointer that's reliable on every
    platform) and best-effort maintains the `latest` symlink. On Windows,
    os.replace over an existing directory symlink can fail even when symlink
    *creation* succeeds — that would leave `latest` frozen at an old recording
    while only latest.txt advances, which is exactly what silently broke the
    TTS hook. latest.txt is therefore the source of truth the hook trusts."""
    latest_link = diburit_home / "latest"
    latest_txt  = diburit_home / "latest.txt"
    # Reliable pointer first.
    _atomic_write(latest_txt, str(target_dir))
    # Best-effort symlink (primary on macOS; nice-to-have on Windows).
    tmp = latest_link.with_name("latest.tmp")
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(target_dir, target_is_directory=True)
        os.replace(tmp, latest_link)
    except OSError:
        # Symlink unsupported or replace failed; latest.txt already covers it.
        # Remove the orphaned tmp symlink so it doesn't accumulate.
        try:
            if tmp.is_symlink() or tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _prune_recordings(keep_n: int, recordings_dir: Path = RECORDINGS_DIR) -> None:
    if keep_n <= 0:
        return
    try:
        dirs = sorted(
            [p for p in recordings_dir.iterdir()
             if p.is_dir() and p.name.startswith("diburit_")],
            key=lambda p: p.name,
            reverse=True,
        )
    except FileNotFoundError:
        return
    for old in dirs[keep_n:]:
        try:
            for child in old.iterdir():
                child.unlink(missing_ok=True)
            old.rmdir()
        except Exception as exc:
            print(f"[Diburit] could not prune {old}: {exc}", file=sys.stderr)
