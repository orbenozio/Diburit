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
# Groq / transcription
# ---------------------------------------------------------------------------

GROQ_URL      = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL    = "whisper-large-v3"
GROQ_LANGUAGE = "he"
GROQ_TIMEOUT  = 60
GROQ_PROMPT   = (
    "זה תמלול של דובר עברית שמשתמש לעיתים במונחים טכניים באנגלית. "
    "מילים כמו commit, git, terminal, install, function, class, repo, "
    "branch, pull request, debug, script, file, folder, server, build, "
    "deploy, log, hook, prompt, token, cache, queue, callback, README — "
    "השאר אותן באנגלית, לא בתעתיק עברי."
)

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

# Base settings shared by both platforms. Each platform adds its own `voice`.
_BASE_SETTINGS: Dict[str, object] = {
    "volume":              0.8,
    "hotkey":              "<cmd>+<shift>+m",
    "hotkey_mode":         HOTKEY_MODE_TOGGLE,
    "max_recordings_kept": 100,
    "speech_rate":         1.0,
    "show_status_rows":    True,
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
    """Point ~/Diburit/latest at target_dir. Tries symlink first; falls back
    to latest.txt on systems where symlinks require elevated rights."""
    latest_link = diburit_home / "latest"
    latest_txt  = diburit_home / "latest.txt"
    try:
        tmp = latest_link.with_name("latest.tmp")
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(target_dir, target_is_directory=True)
        os.replace(tmp, latest_link)
    except OSError:
        _atomic_write(latest_txt, str(target_dir))


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
