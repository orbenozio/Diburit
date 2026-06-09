# CLAUDE.md

## Project Overview

Diburit (דיבורית) is a Hebrew dictation app that ships on both macOS and Windows. A global hotkey records 16 kHz mono audio via `sounddevice`, transcribes it (`language=he`), copies the result to the clipboard, then injects Cmd+V (macOS) / Ctrl+V (Windows) into the frontmost app. Transcription has two backends selected via the `transcription_backend` setting and dispatched by `diburit_core.transcribe()`: **`local`** (default — `faster-whisper`/CTranslate2 on-device, no API key, no cost, offline; model chosen via `local_model`, default ivrit.ai `whisper-large-v3-turbo-ct2`, loaded `int8` on CPU, downloaded from HuggingFace on first use) and **`groq`** (Groq Whisper-large-v3 cloud, needs `GROQ_API_KEY`, pays per use). Local is the default so the app can be distributed without per-user keys or cost. On macOS it builds as a py2app bundle (Python 3.9) with a signed identity (`com.orbenozio.diburit`) so TCC keeps Microphone / Accessibility / AppleEvents permissions stable across rebuilds — the whole reason this project exists, replacing the SayHE/SayIt prototypes that hit unfixable TCC attribution issues.

Companion piece: `tts_assistant.py` is a Claude Code Stop hook that watches `~/Diburit/latest/metadata.json`, detects when the previous user turn was a Diburit paste, and speaks Claude's reply back through `say -v Carmit` + `afplay` (macOS) or `edge-tts` / `gTTS` / `pyttsx3` + `pygame` (Windows).

Current versions (independent): `diburit.py::__version__` (macOS) and `diburit_win.py::__version__` (Windows). On macOS, `setup.py` re-parses the version into the bundle plist.

## Key Files

### Shared
- [diburit_core.py](diburit_core.py) — platform-neutral core: `Utterance`, settings load/save, the `transcribe()` backend dispatcher (`local` faster-whisper / `groq` cloud) + `LOCAL_MODELS` registry, silence detection, pruning, atomic writes, recordings layout. Imported by both `diburit.py` and `diburit_win.py`.
- [platform_compat.py](platform_compat.py) — `sys.platform` shim for clipboard, paste, frontmost-app, notifications, audio playback. Every platform-specific import is **local** inside its function (importing `Quartz` on Windows crashes; importing `win32gui` on macOS crashes).
- [tts_assistant.py](tts_assistant.py) — Claude Code Stop hook. Three-tier readback (SHORT ≤ 220 chars → whole text; PUNCHLINE → last paragraph/sentence; COMPLEX → Groq Llama 3.3 70B summary in 1–2 Hebrew sentences). Uses `fcntl.flock` (macOS) / `filelock` (Windows) on a sibling lockfile to prevent double-speak when the hook fires twice back-to-back.
- [CHANGELOG.md](CHANGELOG.md) — bump the relevant `__version__` (macOS and Windows are independent) and add an entry on every release.

### macOS
- [diburit.py](diburit.py) — main app. `rumps.App` subclass, hotkey listener, recorder, transcription worker, paste pipeline. All UI mutation goes through `main_thread_pump` (rumps.Timer @ 50 Hz) to keep AppKit calls on the main thread.
- [setup.py](setup.py) — py2app build script. Parses `__version__` out of `diburit.py`, sets `LSUIElement=True` (no Dock icon), sets the three required TCC usage strings (`NSMicrophoneUsageDescription`, `NSAppleEventsUsageDescription`), and lists packages that must stay unzipped (sounddevice/soundfile dlopen sibling dylibs at runtime).
- [postbuild.sh](postbuild.sh) — runs after `python setup.py py2app`. Strips duplicate `sounddevice.pyc` / `_sounddevice.pyc` / `soundfile.pyc` out of `python39.zip` so the on-disk `.py` versions win on import (their `__file__` resolves to a real path that dlopen can read from).
- [build_icon.py](build_icon.py) — renders `Diburit.icns` procedurally (squircle, violet→magenta gradient, microphone glyph, sound-wave arcs).
- [requirements.txt](requirements.txt) — macOS runtime deps: rumps, sounddevice, soundfile, numpy, requests, python-dotenv, pyobjc-framework-Quartz, pyobjc-framework-AVFoundation, pynput.

### Windows
- [diburit_win.py](diburit_win.py) — Windows entry point. `pystray` tray icon (state-coloured per recording/transcribing/idle), `tkinter` preferences window, `pynput` global hotkey listener with `win32_event_filter` suppression. Background work pushes onto `self._main_queue`, drained by `tk.after(PUMP_INTERVAL_MS=50)`.
- [requirements_win.txt](requirements_win.txt) — Windows deps: pystray, Pillow, pynput, pyperclip, pyautogui, pywin32, psutil, winotify, pygame, pyttsx3, filelock, edge-tts, gtts.
- [install_win.bat](install_win.bat) — one-shot installer (creates venv, pip-installs `requirements_win.txt`, registers `pythonw diburit_win.py` autostart).
- [tests/test_hotkey_win.py](tests/test_hotkey_win.py) — Windows-only unit tests for `_norm` / `_parse_pynput_chord` / `_hotkey_display`; auto-skip on macOS.

## Runtime Layout (`~/Diburit/`)

- `.env` — holds `GROQ_API_KEY`. Loaded via `python-dotenv` from `DIBURIT_HOME / ".env"`.
- `settings.json` — atomic-written (temp + `os.replace`). Keys: `voice`, `volume`, `hotkey`, `max_recordings_kept`. Defaults: Carmit / 0.8 / `<cmd>+<shift>+m` / 100.
- `recordings/diburit_<YYYYMMDD_HHMMSS_uS>/` — one dir per utterance: `audio.wav`, `transcript.txt`, `metadata.json` (+ `metadata.json.lock` for the TTS hook's flock).
- `latest` — symlink to the most recent recording dir, repointed atomically (temp symlink + `os.replace`). The Stop hook reads `latest/metadata.json`.

## Build & Run

### macOS

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

### Windows

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements_win.txt
.\.venv\Scripts\pythonw.exe diburit_win.py    # runs silently (no console)
```

For dev / quick iteration (logs to stdout instead of `~/Diburit/runtime.log`):

```powershell
.\.venv\Scripts\python.exe diburit_win.py
```

Tests:

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m unittest discover tests -v
```

Logs: `~/Diburit/runtime.log` (open via `notepad $env:USERPROFILE\Diburit\runtime.log`).

## Architecture Notes

- **Threading model.** Hotkey (pynput), recording (sounddevice callback), and transcription (requests POST) all run off-main. They never touch the UI toolkit directly — they push tuples onto `self._main_queue`, drained on the main thread (rumps.Timer @ 50 Hz on macOS; `tk.after(50)` on Windows). Adding new background work? Follow the same pattern.
- **Late-bound paste target.** The frontmost app is queried *after* transcription, not at record-start. Push-to-talk users focus their destination *while* speaking, so binding earlier would paste into the wrong window. Blocklist: Diburit itself, Finder/terminals, empty string.
- **No status notifications.** Diburit must stay silent during the normal record → transcribe → paste cycle — the tray-icon colour communicates state visually. `platform_compat.notify()` is reserved for **errors** only (mic denied, silent recording, transcription failure, paste skipped/blocked). Toasts during normal flow stole focus on Windows and broke the user's flow.
- **TCC (macOS).** Cmd+V is sent via `CGEventPost(kCGHIDEventTap, ...)` against the literal V keycode (9) — layout-independent, and TCC attributes the event to the signed Diburit.app rather than to `osascript`. Microphone + Accessibility + AppleEvents prompts only fire because the three plist usage strings in `setup.py` are present; deleting any of them silently kills the corresponding permission flow.
- **UTF-8 locale leaks (macOS).** Three independent places force `LC_CTYPE=en_US.UTF-8` / `LANG=en_US.UTF-8` in the child env, because under launchd the parent env is stripped: (1) `pbcopy` (else Hebrew → MacRoman mangling), (2) `say` invocation in `tts_assistant.speak()`, (3) `subprocess.run(["say", "-v", "?"], encoding="utf-8", errors="replace")` for the voice list. If you add another subprocess that touches Hebrew, set the locale.
- **Silent-mic defenses.** Two layers: (a) `_audio_is_silent` checks int16 peak vs. `SILENCE_PEAK_THRESHOLD=200` before we even spend a Groq call; (b) `_is_silence_hallucination` filters Whisper's known mute-input outputs ("תודה", "תודה רבה", "שלום", "כן", "you", "תרגום אבישי כהן", …) after transcription.
- **Atomic file writes.** `_atomic_write` (temp + `os.replace`) is used for `settings.json`, `metadata.json`, and the `latest` symlink repoint. Don't bypass it — half-written settings have bitten this project before.
- **Tuning knobs are named constants.** `SILENCE_PEAK_THRESHOLD`, `FOCUS_SETTLE_SEC`, `TRANSCRIBE_RETRY_BACKOFF`, `PUMP_INTERVAL_SEC`, `VOICE_LIST_TIMEOUT`, `TRANSCRIPT_PREVIEW_CHARS`, `NOTIFICATION_PREVIEW_CHARS`, `QUIT_TRANSCRIBE_GRACE_SEC` in `diburit_core.py` / `diburit.py`; `COMPLEX_CODE_FENCE_COUNT`, `PUNCHLINE_MIN_LEN`/`MAX_LEN`, `FALLBACK_SENTENCE_MAX_LEN`, `SAY_RENDER_TIMEOUT_SEC`, `GROQ_SUMMARIZER_*` in `tts_assistant.py`. Change them there, not inline.

## Gotchas

### Cross-platform
- **Two independent versions.** `diburit.py::__version__` and `diburit_win.py::__version__` move separately — a Windows-only fix bumps only the Windows version and vice versa. Add a `CHANGELOG.md` entry per bump.
- **`platform_compat.py` imports are local.** Every `import Quartz` / `import win32gui` / `import pyperclip` is **inside** its function, never at module scope, because the wrong-platform import crashes the file on load. Don't hoist them.

### macOS
- **Python 3.9 is pinned** by the venv and by hardcoded paths in `setup.py` (`/Users/orbenozio/Diburit/.venv/lib/python3.9/site-packages/...`) and `postbuild.sh` (`python39.zip`, `python3.9/`). Upgrading Python means editing both files plus the venv.
- **`postbuild.sh` is not optional.** Skipping it ships a bundle that crashes on first `import sounddevice` because dlopen can't read from inside `python39.zip`. It also **re-signs the bundle** with `Apple Development: Or Benozio (493VVKYUJ4)` (overridable via `DIBURIT_SIGN_IDENTITY`). Without that re-sign the bundle stays ad-hoc — TCC then keys off the binary cdhash and every rebuild revokes Accessibility / Microphone / AppleEvents. Set `DIBURIT_SIGN_IDENTITY=""` to skip signing intentionally.
- **Voice changes are validated.** `on_voice_selected` re-runs `_list_hebrew_voices()` and rejects names that aren't actually installed — prevents a stale menu label from silently breaking both the preview and the Stop hook.
- **Hardcoded absolute paths.** `setup.py`'s `frameworks` list and `postbuild.sh`'s `APP`/`VENV_SP` variables both embed `/Users/orbenozio/Diburit/...`. Anyone cloning to a different path needs to edit these.

### Windows
- **Paste uses `SendInput` with explicit VK codes, not `pyautogui`.** `platform_compat._win_send_ctrl_v` injects `VK_CONTROL=0x11` + `VK_V=0x56` via `SendInput`. `pyautogui.hotkey("ctrl", "v")` resolves "v" through the active keyboard layout, so under a Hebrew layout the resulting WM_KEYDOWN is not interpreted as paste — pyautogui returns no error, the log still says "pasted into <app>", but the target app sees nothing. pyautogui is kept only as a fallback when `SendInput` itself raises.
- **The hotkey must be suppressed at the OS level.** `diburit_win._make_event_filter` is wired into `kb.Listener(win32_event_filter=...)` and calls `listener.suppress_event()` when the chord's modifier groups are satisfied. Without this, F12 reaches VS Code as "Go to Definition" (and other hotkeys leak into other apps), focus shifts to the editor before the Ctrl+V arrives, and the paste lands in the wrong pane.
- **Diburit-itself is detected by PID, not by process name.** `platform_compat.get_frontmost_app` compares the foreground window's PID to `os.getpid()` and returns the literal `"Diburit"` on a self-match. **Do not put `"python"` / `"pythonw"` back in `_PASTE_BLOCKLIST`** — that bricks pasting into any other Python window (REPL, another script, etc.).
- **`pythonw.exe` for prod, `python.exe` for dev.** `pythonw` has no console window (right for autostart); `python` shows stdout/stderr live (right for iteration). Logging always goes to `~/Diburit/runtime.log` either way.
- **Console encoding.** Tests and any direct `python` run need `PYTHONIOENCODING=utf-8` set or Hebrew console output mojibakes (`×‘×“×™×§×”` instead of `בדיקה`).
