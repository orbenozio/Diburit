# Diburit (דיבורית)

A Hebrew dictation app for **macOS and Windows**. Press a global hotkey, speak Hebrew (with English technical terms mixed in if you want), release — the transcription is pasted into whatever app is frontmost.

Works **offline, with no API key, and at no cost**: out of the box Diburit transcribes locally on your own machine using a Hebrew-tuned Whisper model. If you'd rather use the cloud, a Groq backend is one setting away.

Current versions (independent): macOS **1.7.0** · Windows **1.8.0** — see [CHANGELOG.md](CHANGELOG.md).

## What it does

- **Hotkey-driven dictation.** A configurable global hotkey records 16 kHz mono audio via `sounddevice`, then pastes the transcription into the frontmost app.
- **Two transcription backends** (pick in Preferences):
  - **Local (default)** — [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) running on your machine. No API key, no per-use cost, works without internet. Ships with a choice of models, defaulting to [ivrit.ai](https://huggingface.co/ivrit-ai)'s Hebrew-tuned `whisper-large-v3-turbo`.
  - **Groq** — Groq cloud Whisper-large-v3. Fastest and most accurate for Hebrew, but each user needs their own `GROQ_API_KEY` and pays per use.
- **Hebrew vocabulary hint** keeps English technical terms (commit, git, terminal, function, …) in Latin script instead of transliterating them. Applied to both backends.
- **Late-bound paste target.** The frontmost app is queried *after* transcription, not at record-start — push-to-talk users focus their destination *while* speaking, so binding earlier would paste into the wrong window.
- **Silence-hallucination filter** drops Whisper's known mute-input outputs ("תודה.", "כן.", "שלום.", …) so a muted mic doesn't produce ghost text.
- **Companion Claude Code Stop hook** ([tts_assistant.py](tts_assistant.py)) speaks Claude's reply back after a Diburit paste (`say -v Carmit` on macOS; `edge-tts` / `gTTS` / `pyttsx3` on Windows), with three-tier readback (whole text / punchline / Groq Llama summary depending on length).

## Choosing a backend & model

The backend and local model are set in **Preferences** (Windows) and persisted to `~/Diburit/settings.json` (`transcription_backend`, `local_model`). You can also edit that file directly.

| Local model (`local_model`) | Repo | Notes |
| --- | --- | --- |
| `ivrit-turbo` *(default)* | `ivrit-ai/whisper-large-v3-turbo-ct2` | Hebrew-tuned, fast. Best balance for CPU-only machines. |
| `ivrit-large` | `ivrit-ai/whisper-large-v3-ct2` | Hebrew-tuned, most accurate, heavier. |
| `whisper-large-v3` | `large-v3` | Stock multilingual, heavy. |
| `whisper-medium` | `medium` | Stock multilingual, lighter. |
| `whisper-small` | `small` | Stock multilingual, fastest / least accurate. |

Notes on the local backend:

- **First-run download.** The selected model is downloaded from Hugging Face the first time it's used (cached under `~/.cache/huggingface` afterwards). The turbo model is ~1.5 GB. Plan for a one-time wait and an internet connection on first use.
- **Runs on CPU.** Models are loaded as `int8` so they work without a GPU — important on Windows where most users have no CUDA. On a supported GPU it still works, just leaves speed on the table.
- **Latency.** Local transcription on CPU takes a few seconds for a sentence — slower than Groq's sub-second cloud inference, but free and private. Pick `whisper-small`/`ivrit-turbo` on slower machines.

To use Groq instead: set `transcription_backend` to `groq` and put your key in `~/Diburit/.env` as `GROQ_API_KEY=...`.

## Requirements

- macOS (Apple Silicon or Intel) or Windows 10/11
- Python 3.9 on macOS (pinned by absolute paths in [setup.py](setup.py) / [postbuild.sh](postbuild.sh)); Python 3.11 on Windows
- For the **local** backend: nothing extra — `faster-whisper` is in the requirements files (it's CTranslate2-based and does **not** pull in PyTorch, so the install stays small)
- For the **Groq** backend: a Groq API key in `~/Diburit/.env`

## Install & run

### Windows

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements_win.txt
.\.venv\Scripts\pythonw.exe diburit_win.py    # runs silently (no console)
```

`install_win.bat` does this one-shot (creates the venv, installs deps, registers `pythonw diburit_win.py` to autostart). For dev iteration use `python.exe` instead of `pythonw.exe` so stdout/stderr show live. Logs: `~/Diburit/runtime.log`.

### macOS

```bash
python3.9 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python setup.py py2app          # produces dist/Diburit.app
bash postbuild.sh               # MUST run after py2app — fixes dlopen paths and re-signs the bundle
open dist/Diburit.app
```

Dev / quick iteration (no bundle, no TCC stability):

```bash
python diburit.py
```

Icon rebuild (only if you tweak the design): `python build_icon.py`.

## Runtime layout

Everything lives under `~/Diburit/`:

- `.env` — `GROQ_API_KEY=...` (only needed for the Groq backend)
- `settings.json` — voice / volume / hotkey / max_recordings_kept / `transcription_backend` / `local_model`. Atomically written.
- `recordings/diburit_<YYYYMMDD_HHMMSS_uS>/` — one dir per utterance: `audio.wav`, `transcript.txt`, `metadata.json`.
- `latest` — symlink to the most recent recording dir, repointed atomically. The Claude Code Stop hook reads `latest/metadata.json`.

## Gotchas

- **First local run downloads a model.** ~1.5 GB for the turbo model, fetched from Hugging Face and cached. Needs internet that one time; offline afterward.
- **macOS `postbuild.sh` is not optional.** Skipping it ships a bundle that crashes on first `import sounddevice` because dlopen can't read from inside `python39.zip`. It also re-signs the bundle — without that re-sign, every rebuild revokes Microphone / Accessibility / AppleEvents permissions because TCC keys off the binary cdhash.
- **Windows paste uses `SendInput` with explicit VK codes, not `pyautogui`** — under a Hebrew keyboard layout `pyautogui.hotkey("ctrl","v")` silently fails to paste.
- **Hardcoded absolute paths (macOS).** [setup.py](setup.py) and [postbuild.sh](postbuild.sh) embed `/Users/orbenozio/Diburit/...`. Cloning to a different path means editing these.
- **TCC plist usage strings (macOS).** [setup.py](setup.py) sets `NSMicrophoneUsageDescription` and `NSAppleEventsUsageDescription`. Deleting either silently kills the corresponding permission flow.

## Files of interest

- [diburit_core.py](diburit_core.py) — platform-neutral core: settings, the `transcribe()` backend dispatcher (`local` / `groq`), local-model registry, silence detection, atomic writes, recordings layout.
- [diburit.py](diburit.py) — macOS menu-bar app (`rumps.App`); AppKit mutations flow through a `rumps.Timer` @ 50 Hz.
- [diburit_win.py](diburit_win.py) — Windows tray app (`pystray` + `tkinter` preferences + `pynput` hotkey with OS-level suppression).
- [platform_compat.py](platform_compat.py) — `sys.platform` shim for clipboard, paste, frontmost-app, notifications, audio playback (all platform imports are local inside their functions).
- [tts_assistant.py](tts_assistant.py) — Claude Code Stop hook that speaks Claude's reply after a Diburit paste.
- [.claude/CLAUDE.md](.claude/CLAUDE.md) — internal architecture notes for working on this project with Claude Code.

## License

Personal project — no license declared.
</content>
</invoke>
