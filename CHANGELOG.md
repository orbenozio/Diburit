# Changelog

All notable changes to Diburit (דיבורית).
Versions follow [Semantic Versioning](https://semver.org/).

The runtime version lives in `diburit.py::__version__` (macOS) and
`diburit_win.py::__version__` (Windows) — they move independently. On
macOS, `setup.py` reads `diburit.py::__version__` at build time and writes
it into `CFBundleVersion` / `CFBundleShortVersionString`. Bump the
relevant `__version__` and add an entry below when releasing.

## [1.6.1] - 2026-05-17

### Fixed
- **Recording stuck in "recording" state on macOS 26.x.** `_stop_recording`
  called `self._stream.stop()` (which maps to PortAudio's `Pa_StopStream`),
  which under macOS 26.5 with certain input devices waits forever on the
  CoreAudio HAL IO-proc mutex. Because `_stop_recording` runs on the main
  thread (drained from `_main_queue`), the entire AppKit runloop froze
  with the menu-bar icon stuck on the recording state and no `audio.wav`
  ever written. Sampling the stuck process showed the main thread parked
  in `AudioOutputUnitStop -> HALB_Mutex::Lock -> __psynch_mutexwait`.
  Switched to `self._stream.abort()` (`Pa_AbortStream`), which stops the
  stream immediately without waiting for pending callbacks to drain —
  fine here because the audio is already captured in `self._buffer` by
  the input callback. `close()` afterwards is unchanged.

## [win 1.7.0] - 2026-05-16

First Windows release of Diburit. Brings full feature parity with the
macOS app (`Cmd+Shift+M` → `Ctrl+Shift+M`, menu-bar → system-tray) and
adds polish needed for daily use on Windows.

### Added
- **Windows port (`diburit_win.py`).** System-tray app using `pystray`
  (state-coloured icon: violet idle / crimson recording / orange
  transcribing / grey disabled), `tkinter` preferences window, `pynput`
  global hotkey listener. Same recording → transcription → paste
  pipeline as macOS, same `~/Diburit/` runtime layout, same `settings.json`
  schema. Toggle / push-to-talk modes both supported.
- **Platform shim (`platform_compat.py`).** Single module that abstracts
  the ten platform-specific operations (clipboard, paste, frontmost-app,
  notify, audio playback, open-folder, frontmost lookup, …) behind one
  API. Every platform-specific import (`Quartz`, `win32gui`, `pyperclip`)
  is local to its function so importing the module never crashes on the
  wrong OS.
- **Shared core (`diburit_core.py`).** Extracted from `diburit.py`:
  `Utterance`, settings load/save with `_atomic_write`, Groq
  transcription, silence detection, hallucination filter, recordings
  pruning, `latest` symlink repoint. Imported by both `diburit.py` and
  `diburit_win.py`.
- **Windows install path.** `requirements_win.txt` and `install_win.bat`
  for a one-shot venv setup. `pythonw diburit_win.py` runs without a
  console window (for autostart); `python diburit_win.py` shows logs
  live (for development). Either way logs persist to
  `~/Diburit/runtime.log`.
- **Edge TTS / gTTS / pyttsx3 voice options** for the prefs window, with
  the prefs UI letting the user preview each Hebrew voice before
  committing.
- **`tests/test_hotkey_win.py`** — Windows-only unit tests for the
  hotkey chord parser; auto-skip on macOS via `@unittest.skipUnless`.

### Fixed
- **Silent normal flow.** Removed the three status notifications
  ("Recording…", "Recorded Xs, transcribing…", and the final transcript
  preview) that fired on every successful utterance. Windows toasts in
  that volume stole focus from the user's target window mid-flow. The
  tray-icon colour already communicates the same state. Notifications
  are now reserved for *error* states (mic denied, silent recording,
  transcription failed, paste skipped onto a blocked target).
- **Paste fails silently under non-Latin keyboard layouts.**
  `pyautogui.hotkey("ctrl", "v")` routes "v" through the active
  keyboard layout — under a Hebrew layout the resulting WM_KEYDOWN
  isn't interpreted as paste by the receiving app, even though pyautogui
  raises no error and the log records "pasted into X". Replaced with a
  direct `SendInput` call (`platform_compat._win_send_ctrl_v`) using
  explicit `VK_CONTROL=0x11` + `VK_V=0x56`, atomic and layout-independent.
  pyautogui kept only as a fallback when `SendInput` itself raises.
- **Hotkey leaks into the foreground app.** A bare `pynput.keyboard.Listener`
  passes the hotkey through to whatever window has focus, so F12 fires
  VS Code's "Go to Definition" (and other chords leak similarly), pulling
  focus to the editor *before* the `Ctrl+V` arrives. Added
  `_make_event_filter` wired into `Listener(win32_event_filter=...)`:
  modifier groups (`Ctrl`/`Shift`/`Alt`) still pass through normally,
  but the leaf key is suppressed via `listener.suppress_event()` once
  the chord's modifier requirements are satisfied — so other Ctrl/Shift
  shortcuts work everywhere except the exact registered chord.
- **`_PASTE_BLOCKLIST` blocking too much.** Identifying Diburit-itself
  by process name (`"python"` / `"pythonw"`) also blocked pasting into
  any other Python window — REPLs, unrelated scripts, etc. Now
  `platform_compat.get_frontmost_app` compares the foreground HWND's PID
  to `os.getpid()` and returns the literal `"Diburit"` on a self-match;
  the blocklist contains only `Diburit` + terminal apps + empty string.

## [1.6.0] - 2026-05-13

### Added
- **Hebrew + English code-switching support in transcription.** Added
  `GROQ_PROMPT` to `_transcribe_with_groq`, a Hebrew vocabulary hint that
  tells Whisper the speaker mixes English technical terms (commit, git,
  install, terminal, function, repo, branch, pull request, debug,
  script, file, server, build, deploy, log, hook, prompt, token, cache,
  callback, README, etc.) into Hebrew speech and to preserve those words
  in Latin script rather than transliterate them. `language=he` stays
  forced so pure Hebrew still gets the most accurate path; the prompt is
  also in Hebrew per the OpenAI Whisper API requirement that the prompt
  match the audio language.
- **Max Recordings submenu** in the menu bar with presets (25 / 50 / 100
  / 250 / 500 / 1000) and a `Custom…` row that opens a `rumps.Window`.
  Same `[10, 10_000]` clamp as `_load_settings` so a UI-entered custom
  value can't bypass the on-disk guardrail. Persists into
  `settings.json::max_recordings_kept`.
- **`Prune Recordings Now`** menu item: manually triggers the same
  prune that runs after each successful transcription. Useful right
  after dropping the keep-count, since the post-transcription prune
  only fires on the *next* recording. Runs off-main so a slow filesystem
  doesn't stall the menu.
- **`Open Diburit Folder…`** menu item: opens `~/Diburit/` in Finder so
  the user can pop the lid on the .env, settings.json, recordings/, and
  tts_debug.log without needing to remember the path.

### Changed
- **Refactored** the six near-identical `_save_settings({...})` blocks
  scattered across `on_voice_selected` / `_apply_hotkey` /
  `on_toggle_ptt_mode` / `on_volume_selected` / `on_speed_selected` and
  the new max-recordings path into a single `DiburitApp._persist_settings()`
  method. Adding a new setting now only requires updating one place
  instead of six.

### Fixed
- **`postbuild.sh` now invalidates the macOS icon cache** after each
  rebuild. Adds: (1) sanity-check that `Diburit.icns` actually landed in
  `Contents/Resources/`, (2) `touch` on the .app to bump mtime so
  Finder/Dock notice the rebuild, (3) `lsregister -f` so System Settings
  (Login Items, Privacy & Security permission rows) refresh the icon
  without needing a logout. Previously the .icns was being copied
  correctly but the old cached icon kept showing.

## [1.5.0] - 2026-05-13

### Added
- **Playback speed control** as a new `Speed` submenu (6 presets:
  0.9x / 1.0x / 1.15x / 1.3x / 1.5x / 1.75x). gTTS in particular
  reads Hebrew very slowly at its natural cadence, so users typically
  bump this above 1.0. The multiplier is applied at playback time via
  `afplay -r <rate> -q 1`, which uses the high-quality time-stretch
  algorithm so the pitch stays constant (no chipmunk effect) and the
  same setting works uniformly across all three backends (`say`, Edge,
  gTTS). Persisted as `speech_rate` in `settings.json` (default 1.0,
  clamped to 0.5–2.5), read by both the menu-bar preview path
  (`_play_sample*` in `diburit.py`) and the Claude Code Stop hook
  (`tts_assistant.speak()` → `_afplay()`).

## [1.4.0] - 2026-05-13

### Added
- **Two more free TTS backends** alongside Carmit / Edge `he-IL-{Avri,Hila}Neural`:
  - **Edge multilingual neural voices** (`en-US-AvaMultilingualNeural`,
    `en-US-AndrewMultilingualNeural`) under a new `── Edge Neural ──`
    entry pair. These voices auto-detect language per sentence and
    handle Hebrew + English code-switching ("פתח את ה-terminal") far
    more naturally than the he-IL voices, which read embedded English
    with a thick accent. Same `edge:` prefix so the existing render +
    fallback paths in `tts_assistant.speak()` and `_play_sample_edge()`
    handle them unchanged.
  - **gTTS (Google Translate TTS)** as a third backend under a new
    `── Google Translate ──` divider with a single `Hebrew (gTTS)`
    entry stored as `gtts:iw`. Lower quality than Edge but adds a
    no-API-key alternative with different prosody. Selected via the
    new `GTTS_PREFIX = "gtts:"` discriminator in both `diburit.py` and
    `tts_assistant.py`, with a parallel `_render_gtts` / `_play_sample_gtts`
    pair that mirrors the Edge path (renders to MP3 in `/tmp` or
    `~/Diburit`, plays via `afplay`, falls back to Carmit on failure).
- `gtts>=2.5` in `requirements.txt`. `setup.py`'s `packages` list now
  includes `gtts` so the py2app build keeps it (and its `click` /
  `bs4` / `soupsieve` transitive deps) unzipped.

### Changed
- `on_voice_selected` now validates `gtts:` candidates against
  `GTTS_HEBREW_VOICES` (mirroring the existing Edge validation) before
  persisting, so a typo or stale settings.json value cannot silently
  break the preview + Stop hook.
- Menu submenu builder factored the "what counts as a non-`say` voice"
  check into `current_is_remote` so adding the next backend is a
  one-line change.

## [tts_assistant 1.3.1] - 2026-05-13

### Changed
- **Raised `SHORT_THRESHOLD` from 220 to 350 chars.** Replies up to
  ~350 cleaned characters are now read aloud in full instead of falling
  through to the PUNCHLINE path that picks just the last sentence /
  paragraph. The COMPLEX path (`LONG_THRESHOLD=1000`, tables, ≥4 code
  fences) is unchanged, and `PUNCHLINE_MAX_LEN=250` stays as-is — it
  only applies on the > 350-char path now. Docstring updated to match.

## [tts_assistant 1.2.3] - 2026-05-13

### Added
- `GTTS_PREFIX` dispatch in `speak()` plus the `_render_gtts` helper
  (gTTS render → MP3 → afplay, falls back to Carmit on failure).

## [tts_assistant 1.2.2] - 2026-05-13

### Fixed
- **Stop hook no longer speaks the previous turn's reply.** When Claude
  Code fired `Stop` a few hundred milliseconds before flushing the
  current turn's assistant message to the session JSONL, the hook's
  `latest_assistant_text` walk picked up the last *previous* turn's
  reply and spoke that. Replaced the two separate `latest_user_text` +
  `latest_assistant_text` reads with a single `latest_user_and_assistant`
  pass that only considers assistant lines whose JSONL line index is
  greater than the latest user message's index, and added a short poll
  loop (up to 3 s, 100 ms steps) in `main` so we wait for the
  current-turn reply before consuming metadata. A failed poll now
  leaves the metadata available for a later Stop fire instead of
  silently swallowing the turn.

## [1.3.1] - 2026-05-12

### Added
- **PTT mode indicator in the menu bar.** New `ICON_IDLE_PTT = "🎙 ✋"`
  replaces the plain `🎙` while idle when PTT is enabled, so the current
  hotkey mode is visible at a glance without opening the menu. The
  recording / transcribing / disabled icons are unchanged — they
  already convey state unambiguously and don't need the mode suffix.

## [1.3.0] - 2026-05-12

### Added
- **Push-to-Talk (PTT) mode.** New menubar item `Push-to-Talk Mode`
  (checkmark when active). With PTT enabled, holding the hotkey records
  for as long as it's held and releasing it stops the recording — same
  ergonomics as walkie-talkie / Discord. Toggle mode (press to start,
  press again to stop) remains the default. Setting persisted as
  `hotkey_mode: "toggle" | "ptt"` in `~/Diburit/settings.json`.
- **Tap-aware PTT hold filter.** Holds under `PTT_MIN_HOLD_SEC` (180 ms)
  are treated as accidental key taps and the buffer is dropped before
  transcription, so a fat-fingered chord doesn't spend a Groq call on
  ~150 ms of room tone. Independent of (and earlier than) the existing
  `_audio_is_silent` check.

### Changed
- `_QuartzHotkey` now takes an optional `on_released` callback. When
  set, the CGEventTap mask also includes `kCGEventKeyUp` and
  `kCGEventFlagsChanged`, so a chord release fires on either the
  keycode going up *or* any required modifier being dropped — whichever
  happens first (catches the "user lifts Cmd before letting go of M"
  case). The class tracks `_active` to dedupe both presses (no
  key-repeat spam) and releases (one fire even if KeyUp and
  FlagsChanged arrive back to back). `stop()` synthesizes a final
  release if torn down mid-press, so swapping mode or hotkey while
  PTT is held cannot leave the recorder stuck on. Toggle mode passes
  `on_released=None` and still subscribes only to `kCGEventKeyDown`.
- The menu's record item shows `Hold to Record (…)` in PTT mode
  instead of `Start Recording (…)`. Clicking the item still toggles
  manually as a fallback.

### Fixed
- `_hotkey_listener` type annotation referred to
  `pynput_keyboard.GlobalHotKeys`, a symbol that was removed in 1.2.1
  but only survived runtime because `from __future__ import
  annotations` keeps annotations as strings. Now annotated as
  `Optional[_QuartzHotkey]`.

## [1.2.1] - 2026-05-12

### Fixed
- **macOS 26.3 hotkey crash**: replaced `pynput.keyboard.GlobalHotKeys`
  with a `Quartz.CGEventTapCreate` listener installed on the main
  runloop (`_QuartzHotkey` in `diburit.py`). pynput translates incoming
  CGEvents into Key/KeyCode objects via `TISGetInputSourceProperty` on
  its background listener thread, which macOS 26.3's tightened
  `dispatch_assert_queue` enforcement crashes with SIGTRAP on every
  keypress (crash reports show `dispatch_assert_queue_fail →
  TSMGetInputSourceProperty`). The new tap fires its callback on the
  main thread, so the layout query — when it happens at all — is on the
  correct queue. Hotkey spec syntax (`<cmd>+<shift>+m`, `<f19>`) is
  preserved via `_parse_hotkey`, so existing `settings.json` values
  remain valid.
- The Quartz tap also consumes the matched key event (returns `None`
  from the tap callback), so Diburit's hotkey no longer also fires the
  focused app's shortcut for the same chord (the original VS Code +
  Cmd+Shift+M collision is now resolved by the consumer behavior
  instead of a hotkey swap).

### Removed
- **`pynput`** is gone from `requirements.txt` and from `setup.py`'s
  `packages` list — no other code path used it.

## [1.2.0] - 2026-05-12

### Added
- **Microsoft Edge TTS neural voices** (`he-IL-AvriNeural`, `he-IL-HilaNeural`)
  as alternatives to Carmit. Far more natural-sounding, free, no API key
  required. Selected via the menubar Voice submenu under the `── Edge
  Neural ──` divider. `settings.json:voice` now stores either a macOS
  voice name (e.g. `"Carmit"`) or an `"edge:<voice-id>"` identifier, and
  both `_play_sample` in `diburit.py` and `speak` in `tts_assistant.py`
  dispatch on the `edge:` prefix to pick the render backend. Edge TTS
  failures (offline, package missing) fall back to `say -v Carmit` in
  the Stop hook so the user still hears the response.
- **Custom hotkey support.** New `Hotkey` submenu in the menubar with
  preset chords (Cmd+Shift+M, Cmd+Shift+;, Cmd+Shift+', Cmd+Shift+/,
  Cmd+Opt+M, Ctrl+Opt+M, F13, F19) plus a `Custom…` dialog that accepts
  any pynput-format string (e.g. `<cmd>+<shift>+m`, `<f19>`). Selection
  validates via `pynput_keyboard.HotKey.parse`, swaps the global listener
  atomically, and rolls back to the previous hotkey if registration
  fails. Default unchanged (`<cmd>+<shift>+m`); use this to dodge the
  VS Code "Toggle Problems" collision on the default chord.
- `edge-tts>=6.1` in `requirements.txt`. `setup.py` now bundles
  `edge_tts`, `aiohttp`, and `certifi` as packages (not zipped) so the
  py2app build keeps their runtime data files reachable.

### Changed
- **`postbuild.sh` now re-signs the bundle** with the developer's Apple
  Development cert (default: `Apple Development: Or Benozio
  (493VVKYUJ4)`, overridable via `DIBURIT_SIGN_IDENTITY`). py2app
  produces an ad-hoc signed bundle whose TCC identity is the binary
  hash — every rebuild then reads as a new app and revokes the
  Accessibility / Microphone / AppleEvents permissions. Signing with a
  real Apple-issued cert pins the identity to Team ID + Bundle ID, so
  permissions persist across rebuilds. Set `DIBURIT_SIGN_IDENTITY=""`
  to skip (falls back to py2app's ad-hoc signature).

## [1.1.0] - 2026-05-12

### Added
- App icon: `Diburit.icns` rendered procedurally by `build_icon.py`
  (squircle backdrop with a violet -> magenta gradient, white microphone
  glyph, three sound-wave arcs). `setup.py` auto-detects the .icns next
  to itself and passes it to py2app.
- `__version__` constant in `diburit.py` and `tts_assistant.py`. `setup.py`
  parses `diburit.py::__version__` and propagates it to the bundle plist,
  so the About-this-app version and the runtime version cannot drift.
- `CHANGELOG.md` (this file).

### Fixed
- **UTF-8 in `say -v ?`**: `_list_hebrew_voices` now passes
  `encoding="utf-8", errors="replace"` to `subprocess.run`, so the
  Hebrew sample line in Carmit's voice entry no longer crashes the
  voice-list parser under launchd's stripped locale.
- **UTF-8 in TTS hook `say` invocation**: `speak()` now exports
  `LC_CTYPE` / `LANG` set to `en_US.UTF-8` in the child env before
  invoking `say`, mirroring the existing fix on the pbcopy path.
- **Race on metadata consume**: `read_and_consume_metadata` in
  `tts_assistant.py` now wraps the read-check-write in an exclusive
  `fcntl.flock` against a `<metadata>.json.lock` sibling. Two Stop-hook
  invocations firing back to back can no longer both observe the same
  un-consumed metadata and double-speak.
- **Unvalidated voice selection**: `on_voice_selected` now checks the
  candidate name against the live `_list_hebrew_voices()` result before
  persisting and warns via notification if it is not installed. Prevents
  a stale or typo'd menu label from silently breaking both the in-app
  preview and the Claude Code Stop hook.
- **Quit timeout off-by-count**: `on_quit` replaced the `range(40)` loop
  + `0.1`s sleep with an explicit `time.monotonic()` deadline against
  `QUIT_TRANSCRIBE_GRACE_SEC` so the 4-second budget cannot drift if the
  sleep slips.

### Changed
- Magic numbers in `diburit.py` and `tts_assistant.py` are now named
  constants at the top of each file (`SILENCE_PEAK_THRESHOLD`,
  `FOCUS_SETTLE_SEC`, `TRANSCRIBE_RETRY_BACKOFF`, `PUMP_INTERVAL_SEC`,
  `VOICE_LIST_TIMEOUT`, `TRANSCRIPT_PREVIEW_CHARS`,
  `NOTIFICATION_PREVIEW_CHARS`, `QUIT_TRANSCRIBE_GRACE_SEC`,
  `COMPLEX_CODE_FENCE_COUNT`, `PUNCHLINE_MIN_LEN`, `PUNCHLINE_MAX_LEN`,
  `FALLBACK_SENTENCE_MAX_LEN`, `SAY_RENDER_TIMEOUT_SEC`,
  `GROQ_SUMMARIZER_TIMEOUT_SEC`, `GROQ_SUMMARIZER_INPUT_CHAR_LIMIT`,
  `GROQ_SUMMARIZER_MAX_TOKENS`).
- Transcription retry now reads its backoff schedule from the
  `TRANSCRIBE_RETRY_BACKOFF` tuple, so adding a third attempt is a
  one-line change.

## [1.0.0] - 2026-05-11

Initial release. py2app bundle replaces the SayIt / SayHE prototypes
that hit unfixable macOS TCC attribution issues with shell-wrapped
Python.app launches.

- `Cmd+Shift+M` global hotkey via `pynput`
- 16 kHz mono recording via `sounddevice`
- Groq Whisper-large-v3 transcription with `language=he`
- Silence-hallucination filter for muted-mic Whisper artifacts
- Late-bound paste into the frontmost app via `pbcopy` + CGEventPost
- Per-utterance directory + atomic `~/Diburit/latest` symlink
- Claude Code Stop hook (`tts_assistant.py`) with three-tier reading
  (SHORT / PUNCHLINE / COMPLEX) and Groq Llama summarisation for
  long responses
- LaunchAgent (`com.orbenozio.diburit.plist`) with crash-restart and a
  UTF-8 locale env so pbcopy does not mangle Hebrew
