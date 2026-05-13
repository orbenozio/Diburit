# CLAUDE.md

## Project Overview

Diburit (דיבורית) is a macOS menu-bar Hebrew dictation app. `Cmd+Shift+M` records 16 kHz mono audio via `sounddevice`, sends it to Groq Whisper-large-v3 (`language=he`) for transcription, then `pbcopy` + `CGEventPost(Cmd+V)`s the result into whatever app is frontmost. Built as a py2app bundle (Python 3.9) so macOS TCC sees a single signed identity (`com.orbenozio.diburit`) instead of a generic Python.app — that stable identity is the whole reason this project exists, replacing the SayHE/SayIt prototypes that hit unfixable TCC attribution issues.

Companion piece: `tts_assistant.py` is a Claude Code Stop hook that watches `~/Diburit/latest/metadata.json`, detects when the previous user turn was a Diburit paste, and speaks Claude's reply back through `say -v Carmit` + `afplay`.

Current version: **1.1.0** (single source of truth: `diburit.py::__version__`, re-parsed by `setup.py` into the bundle plist).

## Key Files

- [diburit.py](diburit.py) — main app. `rumps.App` subclass, hotkey listener, recorder, transcription worker, paste pipeline. All UI mutation goes through `main_thread_pump` (rumps.Timer @ 50 Hz) to keep AppKit calls on the main thread.
- [tts_assistant.py](tts_assistant.py) — Claude Code Stop hook. Three-tier readback (SHORT ≤ 220 chars → whole text; PUNCHLINE → last paragraph/sentence; COMPLEX → Groq Llama 3.3 70B summary in 1–2 Hebrew sentences). Uses `fcntl.flock` on a sibling lockfile to prevent double-speak when the hook fires twice back-to-back.
- [setup.py](setup.py) — py2app build script. Parses `__version__` out of `diburit.py`, sets `LSUIElement=True` (no Dock icon), sets the three required TCC usage strings (`NSMicrophoneUsageDescription`, `NSAppleEventsUsageDescription`), and lists packages that must stay unzipped (sounddevice/soundfile dlopen sibling dylibs at runtime).
- [postbuild.sh](postbuild.sh) — runs after `python setup.py py2app`. Strips duplicate `sounddevice.pyc` / `_sounddevice.pyc` / `soundfile.pyc` out of `python39.zip` so the on-disk `.py` versions win on import (their `__file__` resolves to a real path that dlopen can read from).
- [build_icon.py](build_icon.py) — renders `Diburit.icns` procedurally (squircle, violet→magenta gradient, microphone glyph, sound-wave arcs).
- [requirements.txt](requirements.txt) — runtime deps: rumps, sounddevice, soundfile, numpy, requests, python-dotenv, pyobjc-framework-Quartz, pyobjc-framework-AVFoundation, pynput.
- [CHANGELOG.md](CHANGELOG.md) — bump this and `__version__` on every release.

## Runtime Layout (`~/Diburit/`)

- `.env` — holds `GROQ_API_KEY`. Loaded via `python-dotenv` from `DIBURIT_HOME / ".env"`.
- `settings.json` — atomic-written (temp + `os.replace`). Keys: `voice`, `volume`, `hotkey`, `max_recordings_kept`. Defaults: Carmit / 0.8 / `<cmd>+<shift>+m` / 100.
- `recordings/diburit_<YYYYMMDD_HHMMSS_uS>/` — one dir per utterance: `audio.wav`, `transcript.txt`, `metadata.json` (+ `metadata.json.lock` for the TTS hook's flock).
- `latest` — symlink to the most recent recording dir, repointed atomically (temp symlink + `os.replace`). The Stop hook reads `latest/metadata.json`.

## Build & Run

Operate inside the local venv (`.venv/`, Python 3.9):

```bash
source .venv/bin/activate
pip install -r requirements.txt
python setup.py py2app          # produces dist/Diburit.app
bash postbuild.sh               # MUST run after py2app — fixes dlopen paths
open dist/Diburit.app
```

Dev / quick iteration:

```bash
python diburit.py               # runs the menu-bar app directly (no bundle)
```

Icon rebuild (only if you tweak the design):

```bash
python build_icon.py            # regenerates Diburit.icns + Diburit.iconset
```

Logs (when running as a LaunchAgent): `diburit.log`, `diburit.err.log` at project root.

## Architecture Notes

- **Threading model.** Hotkey (pynput), recording (sounddevice callback), and transcription (requests POST) all run off-main. They never touch AppKit directly — they push tuples onto `self._main_queue`, and the `rumps.Timer` at 50 Hz drains it on the main thread. Adding new background work? Follow the same pattern.
- **Late-bound paste target.** The frontmost app is queried *after* transcription, not at record-start. Push-to-talk users focus their destination *while* speaking, so binding earlier would paste into the wrong window. Blocklist: Diburit itself, Python, Finder, empty string.
- **TCC.** Cmd+V is sent via `CGEventPost(kCGHIDEventTap, ...)` against the literal V keycode (9) — layout-independent, and TCC attributes the event to the signed Diburit.app rather than to `osascript`. Microphone + Accessibility + AppleEvents prompts only fire because the three plist usage strings in `setup.py` are present; deleting any of them silently kills the corresponding permission flow.
- **UTF-8 locale leaks.** Three independent places force `LC_CTYPE=en_US.UTF-8` / `LANG=en_US.UTF-8` in the child env, because under launchd the parent env is stripped: (1) `pbcopy` (else Hebrew → MacRoman mangling), (2) `say` invocation in `tts_assistant.speak()`, (3) `subprocess.run(["say", "-v", "?"], encoding="utf-8", errors="replace")` for the voice list. If you add another subprocess that touches Hebrew, set the locale.
- **Silent-mic defenses.** Two layers: (a) `_audio_is_silent` checks int16 peak vs. `SILENCE_PEAK_THRESHOLD=200` before we even spend a Groq call; (b) `_is_silence_hallucination` filters Whisper's known mute-input outputs ("תודה", "תודה רבה", "שלום", "כן", "you", "תרגום אבישי כהן", …) after transcription.
- **Atomic file writes.** `_atomic_write` (temp + `os.replace`) is used for `settings.json`, `metadata.json`, and the `latest` symlink repoint. Don't bypass it — half-written settings have bitten this project before.
- **Tuning knobs are named constants.** `SILENCE_PEAK_THRESHOLD`, `FOCUS_SETTLE_SEC`, `TRANSCRIBE_RETRY_BACKOFF`, `PUMP_INTERVAL_SEC`, `VOICE_LIST_TIMEOUT`, `TRANSCRIPT_PREVIEW_CHARS`, `NOTIFICATION_PREVIEW_CHARS`, `QUIT_TRANSCRIBE_GRACE_SEC` in `diburit.py`; `COMPLEX_CODE_FENCE_COUNT`, `PUNCHLINE_MIN_LEN`/`MAX_LEN`, `FALLBACK_SENTENCE_MAX_LEN`, `SAY_RENDER_TIMEOUT_SEC`, `GROQ_SUMMARIZER_*` in `tts_assistant.py`. Change them there, not inline.

## Gotchas

- **Not a git repo.** No `.git`, no remotes. Versioning is manual: bump `__version__` in `diburit.py` and add a `CHANGELOG.md` entry.
- **Python 3.9 is pinned** by the venv and by hardcoded paths in `setup.py` (`/Users/orbenozio/Diburit/.venv/lib/python3.9/site-packages/...`) and `postbuild.sh` (`python39.zip`, `python3.9/`). Upgrading Python means editing both files plus the venv.
- **`postbuild.sh` is not optional.** Skipping it ships a bundle that crashes on first `import sounddevice` because dlopen can't read from inside `python39.zip`. It also **re-signs the bundle** with `Apple Development: Or Benozio (493VVKYUJ4)` (overridable via `DIBURIT_SIGN_IDENTITY`). Without that re-sign the bundle stays ad-hoc — TCC then keys off the binary cdhash and every rebuild revokes Accessibility / Microphone / AppleEvents. Set `DIBURIT_SIGN_IDENTITY=""` to skip signing intentionally.
- **Voice changes are validated.** `on_voice_selected` re-runs `_list_hebrew_voices()` and rejects names that aren't actually installed — prevents a stale menu label from silently breaking both the preview and the Stop hook.
- **Hardcoded absolute paths.** `setup.py`'s `frameworks` list and `postbuild.sh`'s `APP`/`VENV_SP` variables both embed `/Users/orbenozio/Diburit/...`. Anyone cloning to a different path needs to edit these.
