# Diburit (דיבורית)

A macOS menu-bar Hebrew dictation app. Press `Cmd+Shift+M`, speak Hebrew (with English technical terms mixed in if you want), release — the transcription is pasted into whatever app is frontmost.

Built as a py2app bundle so macOS TCC sees a single signed identity (`com.orbenozio.diburit`) instead of a generic `Python.app`. That's the whole reason this project exists, replacing earlier prototypes that hit unfixable TCC attribution issues.

Current version: **1.6.0** — see [CHANGELOG.md](CHANGELOG.md).

## What it does

- **Hotkey-driven dictation.** `Cmd+Shift+M` (configurable) records 16 kHz mono audio via `sounddevice`.
- **Groq Whisper-large-v3** does the transcription with `language=he` forced, plus a Hebrew vocabulary hint that tells Whisper to keep English technical terms (commit, git, terminal, function, …) in Latin script instead of transliterating them.
- **Late-bound paste target.** The frontmost app is queried *after* transcription, not at record-start — push-to-talk users focus their destination *while* speaking, so binding earlier would paste into the wrong window.
- **Silence-hallucination filter** drops Whisper's known mute-input outputs ("תודה.", "כן.", "שלום.", …) so a muted mic doesn't produce ghost text.
- **Companion Claude Code Stop hook** ([tts_assistant.py](tts_assistant.py)) speaks Claude's reply back through `say -v Carmit` after a Diburit paste, with three-tier readback (whole text / punchline / Groq Llama summary depending on length and complexity).

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.9 (the project is pinned to 3.9 by absolute paths in [setup.py](setup.py) and [postbuild.sh](postbuild.sh))
- A Groq API key — put it in `~/Diburit/.env` as `GROQ_API_KEY=...`

## Build & run

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

Icon rebuild (only if you tweak the design):

```bash
python build_icon.py
```

## Runtime layout

Everything lives under `~/Diburit/`:

- `.env` — `GROQ_API_KEY=...`
- `settings.json` — voice / volume / hotkey / max_recordings_kept. Atomically written.
- `recordings/diburit_<YYYYMMDD_HHMMSS_uS>/` — one dir per utterance: `audio.wav`, `transcript.txt`, `metadata.json`.
- `latest` — symlink to the most recent recording dir, repointed atomically. The Claude Code Stop hook reads `latest/metadata.json`.

## Gotchas

- **`postbuild.sh` is not optional.** Skipping it ships a bundle that crashes on first `import sounddevice` because dlopen can't read from inside `python39.zip`. It also re-signs the bundle — without that re-sign, every rebuild revokes Microphone / Accessibility / AppleEvents permissions because TCC keys off the binary cdhash.
- **Hardcoded absolute paths.** [setup.py](setup.py) and [postbuild.sh](postbuild.sh) embed `/Users/orbenozio/Diburit/...`. Anyone cloning to a different path needs to edit these.
- **UTF-8 locale.** Three places force `LC_CTYPE=en_US.UTF-8` in subprocess envs (`pbcopy`, `say`, voice listing) because launchd strips the parent env. If you add a new subprocess that touches Hebrew, set the locale there too.
- **TCC plist usage strings.** [setup.py](setup.py) sets `NSMicrophoneUsageDescription` and `NSAppleEventsUsageDescription`. Deleting either one silently kills the corresponding permission flow.

## Files of interest

- [diburit.py](diburit.py) — main app: `rumps.App` subclass, hotkey listener, recorder, transcription worker, paste pipeline. All AppKit mutations flow through `main_thread_pump` (a `rumps.Timer` @ 50 Hz).
- [tts_assistant.py](tts_assistant.py) — Claude Code Stop hook. Reads `~/Diburit/latest/metadata.json`, classifies the reply, speaks it. Uses `fcntl.flock` to prevent double-speak when the hook fires twice.
- [setup.py](setup.py) — py2app build config. Parses `__version__` out of `diburit.py`, sets `LSUIElement=True` (no Dock icon), TCC usage strings, and the package list that must stay unzipped.
- [postbuild.sh](postbuild.sh) — strips duplicate `.pyc`s out of `python39.zip` so the on-disk `.py`s win on import, then re-signs the bundle.
- [build_icon.py](build_icon.py) — renders `Diburit.icns` procedurally.
- [.claude/CLAUDE.md](.claude/CLAUDE.md) — internal architecture notes for working on this project with Claude Code.

## License

Personal project — no license declared.
