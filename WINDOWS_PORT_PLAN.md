# Diburit — Windows Port Implementation Plan (v2)

> **Purpose:** Detailed spec for porting Diburit to Windows while keeping the macOS build unchanged.
> Agents executing this plan should read the existing source files first, then implement section by section.
> This version incorporates findings from an architect review + gap analysis pass.

---

## Overview

The core pipeline is already cross-platform:
- `sounddevice` — audio recording ✓
- `soundfile` — WAV write ✓
- Groq Whisper API — transcription ✓
- `edge-tts` / `gtts` — TTS voices ✓
- `requests`, `json`, `pathlib`, `threading` — all stdlib/cross-platform ✓

Only the **platform glue** needs replacement:

| # | macOS piece | Windows replacement |
|---|---|---|
| 1 | `rumps` (menu bar app) | `pystray` |
| 2 | AppKit `NSWindow` (prefs) | `tkinter` |
| 3 | `CGEventTap` / Quartz (hotkey) | `pynput.keyboard` (toggle) / `pynput.keyboard.Listener` (PTT) |
| 4 | `pbcopy` (clipboard write) | `pyperclip.copy()` |
| 5 | `CGEventPost(Cmd+V)` (paste) | `pyautogui.hotkey('ctrl','v')` |
| 6 | `osascript` (frontmost app) | `pywin32` `GetForegroundWindow` + `psutil` |
| 7 | `osascript` (notifications) | `winotify` (preferred on Win 11) or `plyer` |
| 8 | `say` + `afplay` (TTS playback) | `edge-tts` render + `pygame.mixer` play (non-blocking thread) |
| 9 | `fcntl.flock` (file lock) | `filelock.FileLock` |
| 10 | `/tmp/` paths | `tempfile.gettempdir()` |

---

## Files to Create / Modify

```
platform_compat.py          ← NEW: unified interface for all 10 platform ops
diburit_win.py              ← NEW: Windows tray app (pystray + tkinter prefs)
tts_assistant.py            ← MODIFY: cross-platform (fcntl, paths, TTS backend)
requirements_win.txt        ← NEW: Windows pip deps
install_win.bat             ← NEW: one-click setup for end users
```

`diburit.py`, `setup.py`, `postbuild.sh`, `requirements.txt` — **do not touch**.

---

## File 1: `platform_compat.py`

### CRITICAL: All platform-specific imports MUST be local (inside function bodies)

If Windows-only imports (`pyperclip`, `pyautogui`, `win32gui`, etc.) are at module level,
`import platform_compat` on macOS will crash with `ModuleNotFoundError`. Every Windows import
must be deferred inside its function body. Same rule applies to the macOS side (`from Quartz import ...`
etc.). Use `if sys.platform == 'darwin':` blocks at the top of each function, not at module level.

### Public API

```python
import sys
from pathlib import Path
from typing import Optional, List, Dict

def copy_to_clipboard(text: str) -> None: ...
def send_paste() -> None:                  # Ctrl+V on Windows, Cmd+V on macOS
def get_frontmost_app() -> Optional[str]: ...
def notify(title: str, message: str) -> None: ...
def open_folder(path: Path) -> None: ...
def list_tts_voices() -> List[Dict[str, str]]: ...   # [{name, id}]
def play_audio_nonblocking(path: Path, volume: float, rate: float) -> None: ...
def acquire_file_lock(lock_path: Path): ...          # context manager
```

### Windows implementations

**`copy_to_clipboard`**
```python
def copy_to_clipboard(text: str) -> None:
    if sys.platform == 'darwin':
        # existing pbcopy subprocess (copy from diburit.py _copy_to_clipboard)
        ...
    else:
        import pyperclip
        pyperclip.copy(text)
```

**`send_paste`**
```python
FOCUS_SETTLE_SEC = 0.15   # match diburit.py constant — do NOT change to 0.05

def send_paste() -> None:
    import time
    time.sleep(FOCUS_SETTLE_SEC)
    if sys.platform == 'darwin':
        # existing CGEventPost(Cmd+V) logic
        ...
    else:
        import pyautogui
        pyautogui.PAUSE = 0        # disable pyautogui's built-in 0.1s inter-call delay
        pyautogui.FAILSAFE = False  # prevent accidental top-left corner kill
        try:
            pyautogui.hotkey('ctrl', 'v')
        except Exception as exc:
            print(f"[Diburit] send_paste failed: {exc}", file=sys.stderr)
```

**`get_frontmost_app`**
```python
def get_frontmost_app() -> Optional[str]:
    if sys.platform == 'darwin':
        # existing osascript call
        ...
    else:
        try:
            import win32gui, win32process, psutil
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name()
            return name.replace('.exe', '').replace('.EXE', '')
        except Exception as exc:
            print(f"[Diburit] get_frontmost_app failed: {exc}", file=sys.stderr)
            return None
```

Note: on Windows, `GetForegroundWindow` returns the **host window** PID, not the child process
inside a terminal. If the user is typing in a Windows Terminal tab, the returned name will be
`WindowsTerminal`, not `cmd` or `pwsh`. This is a known limitation — add `WindowsTerminal` to the
paste blocklist and document that pasting into terminal apps is not supported.

**`notify`**
```python
def notify(title: str, message: str) -> None:
    if sys.platform == 'darwin':
        # existing osascript call
        ...
    else:
        try:
            from winotify import Notification  # pip install winotify
            toast = Notification(app_id="Diburit", title=title, msg=message, duration="short")
            toast.show()
        except Exception as exc:
            print(f"[Diburit] notify failed: {exc}", file=sys.stderr)
```

Note: `plyer` and `win10toast` are NOT recommended for Windows 11 — `winotify` uses the
Windows Runtime Toast API directly and works reliably. The `timeout` parameter is controlled by
OS notification settings, not by the app.

**`open_folder`**
```python
def open_folder(path: Path) -> None:
    if sys.platform == 'darwin':
        import subprocess
        subprocess.run(["open", str(path)], check=False, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import os
        os.startfile(str(path))
```

**`list_tts_voices`**
Returns Edge neural voices (always available, no install needed) plus any installed SAPI5
Hebrew voices. On clean Windows there are usually no Hebrew SAPI5 voices — that's fine because
`edge-tts` is the primary backend.
```python
def list_tts_voices() -> List[Dict[str, str]]:
    if sys.platform == 'darwin':
        # existing _list_hebrew_voices() logic
        ...
    else:
        voices = []
        # SAPI5 via pyttsx3 — broad exception catch because pyttsx3 has known
        # issues on Python 3.12+ and will simply have no Hebrew voices on a
        # clean install.
        try:
            import pyttsx3
            engine = pyttsx3.init()
            for v in engine.getProperty('voices') or []:
                langs = v.languages or []
                # SAPI5 can return hex LCID strings (e.g. "040d" for Hebrew)
                # or BCP-47 strings (e.g. "he-IL"). Check both.
                is_hebrew = any(
                    'he' in str(l).lower() or str(l).lower() in ('040d', '0x040d')
                    for l in langs
                ) or 'hebrew' in v.name.lower()
                if is_hebrew:
                    voices.append({'name': v.name, 'id': v.id})
        except Exception:
            pass
        return voices
```

**`play_audio_nonblocking`**
Must be **non-blocking** — the macOS `afplay` uses `Popen` (fire-and-forget). Blocking the
calling thread (the Claude Code Stop hook or the voice-preview thread) for the full audio
duration would stall the UI or the hook return.
```python
def play_audio_nonblocking(path: Path, volume: float, rate: float) -> None:
    if sys.platform == 'darwin':
        # existing afplay Popen call
        ...
    else:
        import threading
        def _play():
            try:
                import pygame
                pygame.mixer.init()
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.set_volume(max(0.0, min(volume, 1.0)))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():   # NOTE: full qualified name required
                    import time; time.sleep(0.05)
            except Exception as exc:
                import sys
                print(f"[Diburit] play_audio failed: {exc}", file=sys.stderr)
        threading.Thread(target=_play, daemon=True).start()
```

**`acquire_file_lock`**
```python
def acquire_file_lock(lock_path: Path):
    if sys.platform == 'darwin':
        # thin contextmanager wrapping fcntl.flock (copy existing logic)
        ...
    else:
        from filelock import FileLock
        return FileLock(str(lock_path))
```

---

## File 2: `diburit_win.py`

Windows entry point. Duplicates the cross-platform constants/logic from `diburit.py` and
replaces the macOS-specific parts with Windows equivalents.

### Threading model (CRITICAL — one architecture, no mixing)

**Do NOT use a background pump thread.** Tkinter is not thread-safe and any call to
`tk.Toplevel`, widget updates, etc. from a non-main thread will crash with cryptic Tcl errors.

The correct pattern:
```python
def run(self):
    # 1. Create a hidden Tk root — must exist before any Toplevel
    self._root = tk.Tk()
    self._root.withdraw()          # invisible root; only Toplevels are visible

    # 2. Start pystray in a background thread
    self._icon = pystray.Icon('Diburit', self._make_icon(), 'Diburit', self._build_menu())
    self._icon.run_detached()      # pystray owns a Win32 message loop on its own thread

    # 3. Install hotkey listener
    self._install_hotkey_listener()

    # 4. Schedule the pump on the tkinter main loop
    self._root.after(50, self._pump_main_queue)

    # 5. tkinter owns the main thread
    self._root.mainloop()
```

The `_pump_main_queue` reschedules itself:
```python
def _pump_main_queue(self):
    while True:
        try:
            action, payload = self._main_queue.get_nowait()
        except queue.Empty:
            break
        self._handle_action(action, payload)
    self._root.after(50, self._pump_main_queue)   # reschedule
```

All pystray menu callbacks MUST post to `_main_queue`, never touch tkinter directly:
```python
def on_open_preferences(self, icon, item):
    self._main_queue.put(('open_prefs', None))   # handled on main thread
```

### System tray (pystray) — dynamic menu

`pystray` supports dynamic menus by passing a callable instead of a static `Menu`. Use this to
update "Start/Stop Recording", "Last: ..." and "Transcript: ..." rows:

```python
def _build_menu(self):
    # Returns a callable so pystray re-evaluates on each menu open
    def menu_factory():
        record_label = "Stop Recording" if self.recording else \
                       "Hold to Record" if self.hotkey_mode == 'ptt' else "Start Recording"
        items = [
            pystray.MenuItem(f"Status: {'ON' if self.enabled else 'OFF'}",
                             lambda icon, item: self._main_queue.put(('toggle_enabled', None))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"{record_label}  (Ctrl+Shift+M)",
                             lambda icon, item: self._main_queue.put(('toggle_recording', None))),
            pystray.MenuItem("Push-to-Talk Mode",
                             lambda icon, item: self._main_queue.put(('toggle_ptt', None)),
                             checked=lambda item: self.hotkey_mode == 'ptt'),
        ]
        if self.show_status_rows:
            last_label = f"Last: {os.path.basename(os.path.dirname(self.last_recording_path))}" \
                         if self.last_recording_path else "Last recording: (none)"
            transcript_label = (
                f"Transcript: {self.last_transcript[:60]}..."
                if self.last_transcript and len(self.last_transcript) > 60
                else f"Transcript: {self.last_transcript or '(none)'}"
            )
            items += [
                pystray.MenuItem(last_label, None, enabled=False),
                pystray.MenuItem(transcript_label, None, enabled=False),
            ]
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Preferences…",
                             lambda icon, item: self._main_queue.put(('open_prefs', None))),
            pystray.MenuItem("Open Diburit Folder…",
                             lambda icon, item: self._main_queue.put(('open_folder', None))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Diburit",
                             lambda icon, item: self._main_queue.put(('quit', None))),
        ]
        return items
    return pystray.Menu(menu_factory)
```

### Hotkey: toggle mode (pynput)

```python
from pynput import keyboard

def _install_hotkey_listener(self):
    if self.hotkey_mode == 'ptt':
        self._install_ptt_listener()
        return
    # Toggle mode: GlobalHotKeys fires on keydown only
    # Translate <cmd> → <ctrl> in memory; store <cmd> on disk for macOS compatibility
    hotkey_str = self._hotkey_for_platform(self.hotkey)
    try:
        self._hotkey_listener = keyboard.GlobalHotKeys(
            {hotkey_str: lambda: self._main_queue.put(('toggle_recording', None))}
        )
        self._hotkey_listener.start()
    except Exception as exc:
        print(f"[Diburit] hotkey install failed: {exc}", file=sys.stderr)

def _hotkey_for_platform(self, spec: str) -> str:
    # Only translate in memory. settings.json always stores the macOS form
    # (<cmd>+...) so the file can be read by macOS without confusion.
    return spec.replace('<cmd>', '<ctrl>').replace('<command>', '<ctrl>')
```

### Hotkey: PTT mode (pynput.keyboard.Listener)

PTT requires tracking press AND release with modifier-awareness. `GlobalHotKeys` cannot do this.
Use `keyboard.Listener` with manual chord tracking:

```python
def _install_ptt_listener(self):
    # Parse the hotkey spec into a set of pynput key objects
    # e.g. '<ctrl>+<shift>+m' → {Key.ctrl, Key.shift, KeyCode.from_char('m')}
    spec = self._hotkey_for_platform(self.hotkey)
    self._ptt_required_keys = _parse_pynput_chord(spec)  # helper: returns frozenset
    self._ptt_held_keys: set = set()
    self._ptt_active = False
    self._ptt_press_time = 0.0

    def on_press(key):
        canonical = self._hotkey_listener.canonical(key)
        self._ptt_held_keys.add(canonical)
        if (not self._ptt_active
                and self._ptt_required_keys.issubset(self._ptt_held_keys)):
            self._ptt_active = True
            self._ptt_press_time = time.monotonic()
            self._main_queue.put(('start_recording', None))

    def on_release(key):
        canonical = self._hotkey_listener.canonical(key)
        self._ptt_held_keys.discard(canonical)
        if self._ptt_active and canonical in self._ptt_required_keys:
            self._ptt_active = False
            # Enforce PTT_MIN_HOLD_SEC (0.18s) same as macOS
            held_for = time.monotonic() - self._ptt_press_time
            if held_for >= PTT_MIN_HOLD_SEC:
                self._main_queue.put(('stop_recording', None))
            else:
                self._main_queue.put(('cancel_recording', None))

    self._hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    self._hotkey_listener.start()
```

Implement `_parse_pynput_chord(spec: str) -> frozenset` that maps tokens like `ctrl`, `shift`, `m`
to their `pynput.keyboard.Key` / `pynput.keyboard.KeyCode` equivalents.

### Settings / hotkey cross-platform safety

**Always store the macOS `<cmd>+...` form in `settings.json`.**
On Windows, translate to `<ctrl>` only at listener-install time (in `_hotkey_for_platform`).
This means:
- macOS reads a file written by Windows → it sees `<cmd>+<shift>+m` → works correctly
- Windows reads a file written by macOS → it sees `<cmd>+<shift>+m` → translates → works
- No silent binding to a wrong chord on either platform

Default for a fresh Windows install (no existing `settings.json`): store `"<cmd>+<shift>+m"`.

### Paste pipeline

```python
_PASTE_BLOCKLIST_WIN = frozenset({
    'Diburit', 'python', 'pythonw', 'cmd', 'powershell',
    'WindowsTerminal',   # terminal host — pasting into terminal tabs not supported
    '',
})

def _paste_into_frontmost(self) -> Tuple[bool, str]:
    # NOTE: FOCUS_SETTLE_SEC sleep is inside platform_compat.send_paste()
    front = platform_compat.get_frontmost_app()
    if not front or front in _PASTE_BLOCKLIST_WIN:
        print(f"[Diburit] paste skipped (focus: {front!r})", file=sys.stderr)
        return False, front or ''
    platform_compat.send_paste()
    return True, front
```

### Preferences window (tkinter)

```python
class PrefsWindow:
    def __init__(self, app: 'DiburitApp'):
        self._app = app
        self._top = None   # tk.Toplevel, created lazily

    def show(self):
        # MUST be called from the main thread (via _main_queue pump)
        if self._top is not None and self._top.winfo_exists():
            self._top.lift()
            return
        self._top = tk.Toplevel(self._app._root)   # parent = hidden root Tk()
        self._top.title("Diburit — Preferences")
        self._top.resizable(False, False)
        self._build()
        self._populate()

    def _build(self):
        f = ttk.Frame(self._top, padding=20)
        f.grid(sticky='nsew')

        # Voice selector
        ttk.Label(f, text="Voice:").grid(row=0, column=0, sticky='e', padx=8, pady=6)
        self._voice_var = tk.StringVar()
        self._voice_combo = ttk.Combobox(f, textvariable=self._voice_var, width=32, state='readonly')
        self._voice_combo.grid(row=0, column=1, sticky='w')
        self._voice_combo.bind('<<ComboboxSelected>>', self._on_voice_changed)

        # Volume slider
        ttk.Label(f, text="Volume:").grid(row=1, column=0, sticky='e', padx=8, pady=6)
        vol_frame = ttk.Frame(f)
        vol_frame.grid(row=1, column=1, sticky='w')
        self._vol_var = tk.DoubleVar()
        self._vol_slider = ttk.Scale(vol_frame, from_=0.0, to=1.0, variable=self._vol_var,
                                      command=self._on_volume_changed, length=200)
        self._vol_slider.pack(side='left')
        self._vol_label = ttk.Label(vol_frame, text=" 80%", width=5)
        self._vol_label.pack(side='left')

        # Speed slider
        ttk.Label(f, text="Speed:").grid(row=2, column=0, sticky='e', padx=8, pady=6)
        spd_frame = ttk.Frame(f)
        spd_frame.grid(row=2, column=1, sticky='w')
        self._spd_var = tk.DoubleVar()
        self._spd_slider = ttk.Scale(spd_frame, from_=0.5, to=2.5, variable=self._spd_var,
                                      command=self._on_speed_changed, length=200)
        self._spd_slider.pack(side='left')
        self._spd_label = ttk.Label(spd_frame, text="1.00x", width=6)
        self._spd_label.pack(side='left')

        # Hotkey recorder button
        ttk.Label(f, text="Hotkey:").grid(row=3, column=0, sticky='e', padx=8, pady=6)
        self._hotkey_btn = ttk.Button(f, text="", command=self._on_hotkey_record)
        self._hotkey_btn.grid(row=3, column=1, sticky='w')

        # Max recordings
        ttk.Label(f, text="Max recordings:").grid(row=4, column=0, sticky='e', padx=8, pady=6)
        self._max_var = tk.StringVar()
        max_entry = ttk.Entry(f, textvariable=self._max_var, width=8)
        max_entry.grid(row=4, column=1, sticky='w')
        max_entry.bind('<FocusOut>', self._on_max_changed)
        max_entry.bind('<Return>', self._on_max_changed)

        # Show status rows checkbox
        self._show_status_var = tk.BooleanVar()
        ttk.Checkbutton(f, text="Show Last & Transcript in tray menu",
                        variable=self._show_status_var,
                        command=self._on_show_status_changed).grid(
            row=5, column=0, columnspan=2, sticky='w', padx=8, pady=6)

        # Buttons
        btn_frame = ttk.Frame(f)
        btn_frame.grid(row=6, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="Prune Recordings Now", command=self._on_prune).pack(side='left', padx=4)
        ttk.Button(btn_frame, text="Done", command=self._top.destroy).pack(side='left', padx=4)

    def _populate(self):
        a = self._app
        # Build voice list: Edge voices + any installed SAPI5 Hebrew voices
        from diburit_win import EDGE_HEBREW_VOICES, GTTS_HEBREW_VOICES
        all_voices = list(EDGE_HEBREW_VOICES) + list(GTTS_HEBREW_VOICES)
        sapi_voices = platform_compat.list_tts_voices()
        for v in sapi_voices:
            all_voices.append((v['name'], v['id']))
        self._voice_ids = [vid for _, vid in all_voices]
        self._voice_combo['values'] = [lbl for lbl, _ in all_voices]
        if a.voice in self._voice_ids:
            self._voice_combo.current(self._voice_ids.index(a.voice))
        self._vol_var.set(a.volume)
        self._vol_label.config(text=f"{int(round(a.volume * 100)):3d}%")
        self._spd_var.set(a.speech_rate)
        self._spd_label.config(text=f"{a.speech_rate:.2f}x")
        self._hotkey_btn.config(text=a.hotkey)
        self._max_var.set(str(a.max_recordings_kept))
        self._show_status_var.set(a.show_status_rows)

    # ... action handlers (_on_voice_changed, _on_volume_changed, etc.)
    # Follow the same pattern as macOS _PrefsWindow: update app state, call _persist_settings()
```

### Quit with transcription grace period

```python
def on_quit(self):
    if self.recording:
        self._stop_recording()
    if self.transcribing:
        platform_compat.notify("Diburit", "Finishing transcription...")
        deadline = time.monotonic() + QUIT_TRANSCRIBE_GRACE_SEC  # 4.0s
        while self.transcribing and time.monotonic() < deadline:
            time.sleep(0.1)
    self._icon.stop()         # stop pystray
    self._root.quit()         # stop tkinter mainloop
```

### Mic permission error handling

```python
try:
    stream = sd.InputStream(...)
    stream.start()
except sd.PortAudioError as exc:
    msg = str(exc)
    if 'Invalid device' in msg or 'Unanticipated' in msg:
        platform_compat.notify("Diburit",
            "Mic access denied — check Settings → Privacy → Microphone")
    else:
        platform_compat.notify("Diburit", f"Could not start mic: {msg[:100]}")
    return
```

### Console window suppression

Launch with `pythonw.exe`, not `python.exe`. Add to `install_win.bat`:
```batch
REM Use pythonw.exe to suppress the console window
set PYTHON_WIN=%CD%\.venv\Scripts\pythonw.exe
```
And create the startup shortcut using `pythonw.exe`:
```batch
powershell -Command "^
  $s=(New-Object -COM WScript.Shell).CreateShortcut($env:APPDATA+'\Microsoft\Windows\Start Menu\Programs\Startup\Diburit.lnk');^
  $s.TargetPath='%CD%\.venv\Scripts\pythonw.exe';^
  $s.Arguments='diburit_win.py';^
  $s.WorkingDirectory='%CD%';^
  $s.Save()"
```

---

## File 3: Update `tts_assistant.py` for cross-platform

### Change 1: `latest.txt` consumer (CRITICAL — no code existed in v1)

```python
import sys

def _resolve_latest_dir() -> Optional[Path]:
    """Return the Path of the most recent recording directory.
    macOS: follows the `latest` symlink.
    Windows: reads `latest.txt` which contains the absolute path as text."""
    if sys.platform == 'darwin':
        if not LATEST_DIR.exists():
            return None
        return LATEST_DIR
    else:
        latest_txt = DIBURIT_HOME / 'latest.txt'
        if not latest_txt.exists():
            return None
        try:
            p = Path(latest_txt.read_text(encoding='utf-8').strip())
            return p if p.is_dir() else None
        except OSError:
            return None
```

Replace all direct uses of `LATEST_DIR` in `read_and_consume_metadata` with `_resolve_latest_dir()`:
```python
def read_and_consume_metadata(latest_user_text: Optional[str]) -> Optional[dict]:
    recording_dir = _resolve_latest_dir()
    if recording_dir is None:
        return None
    metadata_path = recording_dir / METADATA_NAME
    if not metadata_path.exists():
        return None
    lock_path = metadata_path.with_suffix(".json.lock")
    # ... rest of function unchanged, using metadata_path and lock_path
```

Also update the module-level constant to handle both platforms:
```python
LATEST_DIR = DIBURIT_HOME / "latest"   # macOS symlink; unused on Windows
```

### Change 2: `find_session_jsonl` Windows path (CRITICAL)

On Windows, Claude Code stores session JSONL files in `%APPDATA%\Claude\projects\`,
NOT in `~/.claude/projects/`.

```python
def find_session_jsonl(session_id: str) -> Optional[Path]:
    search_roots = [Path.home() / ".claude" / "projects"]   # macOS
    if sys.platform != 'darwin':
        import os
        appdata = os.environ.get("APPDATA")
        if appdata:
            search_roots.append(Path(appdata) / "Claude" / "projects")
    for root in search_roots:
        matches = list(root.glob(f"*/{session_id}.jsonl")) if root.exists() else []
        if matches:
            return matches[0]
    return None
```

### Change 3: `fcntl` → `filelock`

Replace the entire lock block in `read_and_consume_metadata`:

```python
# BEFORE (macOS only):
import fcntl
lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    # ... body ...
finally:
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)

# AFTER (cross-platform):
from filelock import FileLock
with FileLock(str(lock_path), timeout=5):
    # ... same body ...
```

### Change 4: TTS backend — platform-conditional (CRITICAL)

The Windows replacement for `_render_say` + `_afplay` must be non-blocking (match macOS Popen).
Primary backend on Windows is always `edge-tts` — there are no reliable local Hebrew TTS voices
on a clean Windows install. `pyttsx3` / SAPI5 is only attempted if the user has explicitly
installed a Hebrew voice pack.

```python
import sys

if sys.platform == 'darwin':
    def _render_say(text: str, voice: str, env: dict) -> bool:
        # ... existing macOS implementation unchanged ...

    def _afplay_nonblocking(path: Path, volume: float, rate: float) -> None:
        # ... existing Popen call unchanged ...

else:
    def _render_say(text: str, voice: str, env: dict) -> bool:
        """Windows: try SAPI5 via pyttsx3. Only works if a Hebrew voice is installed."""
        try:
            import pyttsx3
            engine = pyttsx3.init()
            # Find a voice matching the requested name, or use first available
            for v in engine.getProperty('voices') or []:
                if voice.lower() in v.name.lower() or voice.lower() in (v.id or '').lower():
                    engine.setProperty('voice', v.id)
                    break
            engine.save_to_file(text, str(SPEECH_MP3))
            engine.runAndWait()
            return SPEECH_MP3.exists() and SPEECH_MP3.stat().st_size > 0
        except Exception as exc:
            print(f"[tts] pyttsx3 failed: {exc}", file=sys.stderr)
            return False

    def _afplay_nonblocking(path: Path, volume: float, rate: float) -> None:
        """Windows: play via pygame in a daemon thread (non-blocking like macOS Popen)."""
        import threading
        def _play():
            try:
                import pygame
                pygame.mixer.init()
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.set_volume(max(0.0, min(volume, 1.0)))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    import time; time.sleep(0.05)
            except Exception as exc:
                print(f"[tts] pygame play failed: {exc}", file=sys.stderr)
        threading.Thread(target=_play, daemon=True).start()
```

Update `speak()` to use the renamed `_afplay_nonblocking` and add an explicit Windows fallback:
```python
def speak(text: str) -> None:
    if not text.strip():
        return
    voice, volume, rate = load_voice_settings()
    env = os.environ.copy()
    env.setdefault("LC_CTYPE", "en_US.UTF-8")
    env.setdefault("LANG", "en_US.UTF-8")

    if voice.startswith(EDGE_PREFIX):
        edge_voice = voice[len(EDGE_PREFIX):]
        if _render_edge_tts(text, edge_voice, SPEECH_MP3):
            _afplay_nonblocking(SPEECH_MP3, volume, rate)
            return
        print(f"[tts] edge tts failed, falling back", file=sys.stderr)
        # On macOS: fall back to say -v Carmit
        # On Windows: fall back to pyttsx3, or silent if no Hebrew voice installed
        if sys.platform == 'darwin':
            if _render_say(text, DEFAULT_VOICE, env):
                _afplay_nonblocking(SPEECH_AIFF, volume, rate)
        else:
            if _render_say(text, DEFAULT_VOICE, env):
                _afplay_nonblocking(SPEECH_MP3, volume, rate)
        return

    if voice.startswith(GTTS_PREFIX):
        gtts_lang = voice[len(GTTS_PREFIX):]
        if _render_gtts(text, gtts_lang, SPEECH_MP3):
            _afplay_nonblocking(SPEECH_MP3, volume, rate)
            return
        print(f"[tts] gtts failed, falling back", file=sys.stderr)
        if sys.platform == 'darwin':
            if _render_say(text, DEFAULT_VOICE, env):
                _afplay_nonblocking(SPEECH_AIFF, volume, rate)
        else:
            if _render_say(text, DEFAULT_VOICE, env):
                _afplay_nonblocking(SPEECH_MP3, volume, rate)
        return

    # Plain voice name (macOS say / Windows pyttsx3)
    out_path = SPEECH_AIFF if sys.platform == 'darwin' else SPEECH_MP3
    if _render_say(text, voice, env):
        _afplay_nonblocking(out_path, volume, rate)
```

### Change 5: Temp paths

```python
import tempfile
_TMP = Path(tempfile.gettempdir())
SPEECH_AIFF = _TMP / "diburit_tts.aiff"   # macOS only
SPEECH_MP3  = _TMP / "diburit_tts.mp3"
```

---

## File 4: `requirements_win.txt`

```
# Core (same as macOS)
numpy
requests
sounddevice
soundfile
python-dotenv
edge-tts
gtts

# Windows-specific replacements
pystray>=0.19.5
Pillow>=10.0.0
pynput>=1.7.6
pyperclip>=1.8.2
pyautogui>=0.9.54
pywin32>=306
psutil>=5.9.0
winotify>=1.1.0
pygame>=2.5.0
pyttsx3>=2.90
filelock>=3.13.0
```

Note: `tkinter` is stdlib — no pip entry needed. If it is missing (Windows Store Python,
custom install with tcl/tk deselected), `install_win.bat` will detect and report it.

---

## File 5: `install_win.bat`

```batch
@echo off
setlocal
echo === Diburit Windows Installer ===
echo.

REM ── Python check ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.9+ from python.org
    echo        Make sure to check "Add python.exe to PATH" during install.
    pause & exit /b 1
)

REM ── tkinter check ─────────────────────────────────────────────────────────
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo ERROR: tkinter is not available.
    echo        Re-run the Python installer and enable "tcl/tk and IDLE".
    pause & exit /b 1
)

REM ── Virtual environment ───────────────────────────────────────────────────
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

REM ── Dependencies ──────────────────────────────────────────────────────────
echo Installing dependencies...
pip install -r requirements_win.txt
if errorlevel 1 (
    echo ERROR: pip install failed. See above for details.
    pause & exit /b 1
)

REM ── Diburit home directories ──────────────────────────────────────────────
if not exist "%USERPROFILE%\Diburit" mkdir "%USERPROFILE%\Diburit"
if not exist "%USERPROFILE%\Diburit\recordings" mkdir "%USERPROFILE%\Diburit\recordings"

REM ── GROQ_API_KEY check ────────────────────────────────────────────────────
if not exist "%USERPROFILE%\Diburit\.env" (
    echo.
    echo ACTION REQUIRED: Create the file  %USERPROFILE%\Diburit\.env
    echo with this content:
    echo     GROQ_API_KEY=your_key_here
    echo Get a free key at https://console.groq.com
)

REM ── Windows Defender note ────────────────────────────────────────────────
echo.
echo NOTE: Diburit installs a global keyboard hook to detect your hotkey.
echo       Windows Defender or your antivirus may flag this as suspicious.
echo       If Diburit is quarantined or blocked, add an exclusion in:
echo       Windows Security → Virus and threat protection → Exclusions
echo       and add the folder: %CD%

REM ── Startup shortcut (optional) ───────────────────────────────────────────
echo.
set /p ADD_STARTUP="Add Diburit to Windows startup? [y/N]: "
if /i "%ADD_STARTUP%"=="y" (
    powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut($env:APPDATA+'\Microsoft\Windows\Start Menu\Programs\Startup\Diburit.lnk'); $s.TargetPath='%CD%\.venv\Scripts\pythonw.exe'; $s.Arguments='diburit_win.py'; $s.WorkingDirectory='%CD%'; $s.Save()"
    echo Startup shortcut created.
)

echo.
echo Installation complete!
echo To start Diburit now:  .venv\Scripts\pythonw.exe diburit_win.py
echo.
pause
```

---

## Symlinks (`latest`) on Windows

First attempt a real symlink (works if Developer Mode or admin):
```python
def _repoint_latest(target_dir: Path) -> None:
    if sys.platform == 'darwin':
        # existing symlink logic (unchanged)
        tmp = LATEST_DIR_SYMLINK.with_name(LATEST_DIR_SYMLINK.name + ".tmp")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(target_dir)
        os.replace(tmp, LATEST_DIR_SYMLINK)
    else:
        # Try symlink first; fall back to latest.txt
        latest_link = DIBURIT_HOME / 'latest'
        latest_txt  = DIBURIT_HOME / 'latest.txt'
        try:
            tmp = latest_link.with_name('latest.tmp')
            if tmp.exists() or tmp.is_symlink(): tmp.unlink()
            tmp.symlink_to(target_dir, target_is_directory=True)
            os.replace(tmp, latest_link)
        except OSError:
            # Developer Mode not enabled — fall back to text file
            _atomic_write(latest_txt, str(target_dir))
```

`tts_assistant.py`'s `_resolve_latest_dir()` checks both `latest` (symlink) and `latest.txt` on
Windows, so both paths are handled transparently.

---

## Known Limitations on Windows (document in README)

1. **Pasting into terminal apps** (Windows Terminal, cmd, PowerShell) is not supported because
   `GetForegroundWindow` returns the host process PID, not the shell inside. Those are in the blocklist.

2. **Global hotkey in elevated windows**: pynput's `WH_KEYBOARD_LL` hook cannot intercept keypresses
   directed at UAC prompts or applications running as Administrator. The hotkey will be ignored in
   those windows.

3. **Antivirus false positives**: pynput's keyboard hook and pyautogui's `SendInput` both trigger
   keylogger heuristics in some AV/EDR products. Users on enterprise machines may need IT whitelisting.

4. **Speech rate** (`afplay -r` equivalent): pygame does not natively support playback rate change.
   The `rate` setting is stored and respected on macOS but ignored on Windows (MVP). A future
   improvement would use `pydub`/`librosa` to pre-process the audio before playback.

5. **Hebrew TTS fallback**: If `edge-tts` fails (no network), there is no local Hebrew TTS fallback
   on a clean Windows install. The app will silently skip speaking. Users can install the Hebrew
   language pack (Settings → Time & Language → Language & Region → Hebrew) to enable SAPI5 fallback.

---

## Testing Checklist

- [ ] `pythonw.exe diburit_win.py` starts without import errors
- [ ] Tray icon appears in system tray
- [ ] `Ctrl+Shift+M` triggers recording (sounddevice opens mic)
- [ ] Recording stops on second `Ctrl+Shift+M`; Groq transcribes
- [ ] Transcript is copied to clipboard (`pyperclip`)
- [ ] `Ctrl+V` is sent to the frontmost app (`pyautogui`) — verify paste into Notepad
- [ ] Paste is SKIPPED for `WindowsTerminal` (blocklist check)
- [ ] Notification appears (winotify toast)
- [ ] PTT mode: hold `Ctrl+Shift+M` → records → release → transcribes
- [ ] PTT: tap shorter than 180ms is dropped without Groq call
- [ ] PTT: release modifier-key before trigger key still stops recording
- [ ] Silent mic: `_audio_is_silent` fires, notification "check Settings → Privacy → Microphone"
- [ ] Preferences window opens from tray menu (tkinter Toplevel)
- [ ] Voice preview plays (edge-tts + pygame non-blocking)
- [ ] Volume and speed sliders update app state and persist to settings.json
- [ ] Hotkey recorder captures new chord and reinstalls listener
- [ ] `show_status_rows = False` hides Last/Transcript rows in tray menu
- [ ] Prune recordings works
- [ ] Quit waits for in-flight transcription (up to 4s grace)
- [ ] `~/Diburit/recordings/` gets new dir per utterance
- [ ] `~/Diburit/latest.txt` is updated after each recording
- [ ] `tts_assistant.py` runs on Windows without `fcntl` errors
- [ ] `tts_assistant.py` finds Claude Code sessions in `%APPDATA%\Claude\projects\`
- [ ] macOS reads a `settings.json` written by Windows without breaking hotkey binding
- [ ] `install_win.bat` detects missing tkinter and prints helpful error

---

## What NOT to Change

- `diburit.py` — macOS version; do not add Windows guards
- `setup.py` / `postbuild.sh` — macOS build tooling
- `requirements.txt` — macOS deps
- `CHANGELOG.md` — update with "v1.7.0 — Windows support" entry when done

---

## Implementation Order

1. `platform_compat.py` — foundation (all other files depend on it)
2. `requirements_win.txt` + `install_win.bat`
3. `tts_assistant.py` changes (self-contained, smaller scope)
4. `diburit_win.py` — tray + toggle hotkey + recording pipeline
5. PTT mode (pynput Listener)
6. Preferences window (tkinter) in `diburit_win.py`
7. End-to-end test against checklist above
