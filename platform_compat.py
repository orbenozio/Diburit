"""Platform compatibility shim for Diburit.

Provides a unified API for the ten platform-specific operations so that
diburit_win.py and tts_assistant.py do not need direct macOS/Windows
conditionals scattered through them.

IMPORTANT: Every platform-specific import (pyperclip, win32gui, fcntl, …)
is a LOCAL import inside the function body. Module-level platform imports
would crash the other platform on `import platform_compat`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

# Shared constant used by send_paste() so callers don't need their own sleep.
FOCUS_SETTLE_SEC = 0.15


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def copy_to_clipboard(text: str) -> None:
    if sys.platform == "darwin":
        env = os.environ.copy()
        env.setdefault("LC_CTYPE", "en_US.UTF-8")
        env.setdefault("LANG", "en_US.UTF-8")
        try:
            subprocess.run(
                ["pbcopy"], input=text.encode("utf-8"),
                check=False, timeout=2, env=env,
            )
        except Exception as exc:
            print(f"[Diburit] pbcopy failed: {exc}", file=sys.stderr)
    else:
        try:
            import pyperclip  # type: ignore
            pyperclip.copy(text)
        except Exception as exc:
            print(f"[Diburit] pyperclip failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Paste (Cmd+V / Ctrl+V) — includes the focus-settle delay
# ---------------------------------------------------------------------------

def _win_send_ctrl_v() -> None:
    """Inject Ctrl+V via SendInput with explicit VK codes.

    Uses VK_CONTROL (0x11) + VK_V (0x56), independent of the active
    keyboard layout. SendInput is atomic and more reliable than
    pyautogui's keybd_event path on non-Latin layouts (e.g. Hebrew)."""
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD   = 1
    KEYEVENTF_KEYUP  = 0x0002
    VK_CONTROL       = 0x11
    VK_V             = 0x56

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk",         wintypes.WORD),
            ("wScan",       wintypes.WORD),
            ("dwFlags",     wintypes.DWORD),
            ("time",        wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx",          wintypes.LONG),
            ("dy",          wintypes.LONG),
            ("mouseData",   wintypes.DWORD),
            ("dwFlags",     wintypes.DWORD),
            ("time",        wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg",    wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]

    def _ki(vk: int, flags: int) -> "INPUT":
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        return inp

    events = (INPUT * 4)(
        _ki(VK_CONTROL, 0),
        _ki(VK_V,       0),
        _ki(VK_V,       KEYEVENTF_KEYUP),
        _ki(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype  = wintypes.UINT
    sent = user32.SendInput(4, events, ctypes.sizeof(INPUT))
    if sent != 4:
        err = ctypes.get_last_error()
        raise OSError(f"SendInput sent only {sent}/4 events (GetLastError={err})")


def send_paste() -> None:
    """Send the paste hotkey to the foreground app.
    Waits FOCUS_SETTLE_SEC first so an in-flight focus change can land."""
    time.sleep(FOCUS_SETTLE_SEC)
    if sys.platform == "darwin":
        try:
            from Quartz import (  # type: ignore
                CGEventCreateKeyboardEvent,
                CGEventPost,
                CGEventSetFlags,
                kCGEventFlagMaskCommand,
                kCGHIDEventTap,
            )
            _V = 9  # kVK_ANSI_V
            down = CGEventCreateKeyboardEvent(None, _V, True)
            CGEventSetFlags(down, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, down)
            up = CGEventCreateKeyboardEvent(None, _V, False)
            CGEventSetFlags(up, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, up)
        except Exception as exc:
            print(f"[Diburit] CGEventPost failed: {exc}", file=sys.stderr)
    else:
        try:
            _win_send_ctrl_v()
        except Exception as exc:
            print(f"[Diburit] SendInput Ctrl+V failed: {exc}", file=sys.stderr)
            try:
                import pyautogui  # type: ignore
                pyautogui.PAUSE = 0
                pyautogui.FAILSAFE = False
                pyautogui.hotkey("ctrl", "v")
            except Exception as exc2:
                print(f"[Diburit] pyautogui fallback failed: {exc2}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Gradual typing (character-by-character injection)
# ---------------------------------------------------------------------------

# Bounds for the typing speed, in characters per second. 1000 cps is
# effectively "instant"; the floor keeps a stray 0 from hanging forever.
MIN_TYPE_CPS = 1.0
MAX_TYPE_CPS = 1000.0


def _win_type_text(text: str, cps: float) -> None:
    """Type `text` into the foreground app one character at a time via
    SendInput. Uses KEYEVENTF_UNICODE so each character is delivered by its
    code point regardless of the active keyboard layout - the same reason
    paste uses VK codes, and what makes Hebrew inject correctly under a Hebrew
    or English layout. Newlines are sent as VK_RETURN (a literal '\\n' unicode
    event does not produce an Enter in most apps)."""
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD   = 1
    KEYEVENTF_KEYUP  = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_RETURN        = 0x0D
    VK_TAB           = 0x09

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk",         wintypes.WORD),
            ("wScan",       wintypes.WORD),
            ("dwFlags",     wintypes.DWORD),
            ("time",        wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype  = wintypes.UINT

    def _send(*events: "INPUT") -> None:
        arr = (INPUT * len(events))(*events)
        user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))

    def _vk(vk: int) -> None:
        down = INPUT(type=INPUT_KEYBOARD)
        down.u.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
        up = INPUT(type=INPUT_KEYBOARD)
        up.u.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
        _send(down, up)

    def _unicode(cp: int) -> None:
        down = INPUT(type=INPUT_KEYBOARD)
        down.u.ki = KEYBDINPUT(wVk=0, wScan=cp, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
        up = INPUT(type=INPUT_KEYBOARD)
        up.u.ki = KEYBDINPUT(wVk=0, wScan=cp, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
        _send(down, up)

    delay = 1.0 / max(MIN_TYPE_CPS, min(cps, MAX_TYPE_CPS))
    for ch in text:
        if ch in ("\n", "\r"):
            _vk(VK_RETURN)
        elif ch == "\t":
            _vk(VK_TAB)
        else:
            # Send each UTF-16 code unit (handles non-BMP via surrogate pair).
            for unit in _utf16_units(ch):
                _unicode(unit)
        time.sleep(delay)


def _utf16_units(ch: str) -> List[int]:
    """UTF-16 code units for a single character (one int for BMP, a surrogate
    pair for astral code points like emoji)."""
    b = ch.encode("utf-16-le")
    return [b[i] | (b[i + 1] << 8) for i in range(0, len(b), 2)]


def type_text(text: str, cps: float) -> None:
    """Inject `text` into the foreground app character-by-character at `cps`
    characters per second (a human-typing effect), instead of an instant paste.
    Blocking - callers run it on the background transcription thread. Waits the
    focus-settle delay first, mirroring send_paste()."""
    if not text:
        return
    time.sleep(FOCUS_SETTLE_SEC)
    if sys.platform == "darwin":
        _mac_type_text(text, cps)
    else:
        _win_type_text(text, cps)


def _mac_type_text(text: str, cps: float) -> None:
    """macOS character-by-character typing via Quartz Unicode key events."""
    try:
        from Quartz import (  # type: ignore
            CGEventCreateKeyboardEvent,
            CGEventKeyboardSetUnicodeString,
            CGEventPost,
            kCGHIDEventTap,
        )
    except Exception as exc:
        print(f"[Diburit] mac type_text unavailable: {exc}", file=sys.stderr)
        return
    delay = 1.0 / max(MIN_TYPE_CPS, min(cps, MAX_TYPE_CPS))
    for ch in text:
        # pyobjc accepts the Python string directly and handles the UTF-16
        # buffer; length is the number of UTF-16 units in the character.
        n_units = len(ch.encode("utf-16-le")) // 2
        down = CGEventCreateKeyboardEvent(None, 0, True)
        CGEventKeyboardSetUnicodeString(down, n_units, ch)
        CGEventPost(kCGHIDEventTap, down)
        up = CGEventCreateKeyboardEvent(None, 0, False)
        CGEventKeyboardSetUnicodeString(up, n_units, ch)
        CGEventPost(kCGHIDEventTap, up)
        time.sleep(delay)


# ---------------------------------------------------------------------------
# Frontmost / foreground application name
# ---------------------------------------------------------------------------

def get_frontmost_app() -> Optional[str]:
    if sys.platform == "darwin":
        script = (
            'tell application "System Events" to '
            'name of first application process whose frontmost is true'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
        except Exception as exc:
            print(f"[Diburit] frontmost lookup failed: {exc}", file=sys.stderr)
            return None
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None
    else:
        try:
            import win32gui       # type: ignore
            import win32process   # type: ignore
            import psutil         # type: ignore
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == os.getpid():
                return "Diburit"
            name = psutil.Process(pid).name()
            return name.replace(".exe", "").replace(".EXE", "")
        except Exception as exc:
            print(f"[Diburit] get_frontmost_app failed: {exc}", file=sys.stderr)
            return None


# ---------------------------------------------------------------------------
# Desktop notifications
# ---------------------------------------------------------------------------

def notify(title: str, message: str) -> None:
    if sys.platform == "darwin":
        def _q(s: str) -> str:
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
        script = f"display notification {_q(message)} with title {_q(title)}"
        try:
            subprocess.run(
                ["osascript", "-e", script], check=False, timeout=2,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"[Diburit] notification failed: {exc}", file=sys.stderr)
    else:
        try:
            from winotify import Notification  # type: ignore
            Notification(app_id="Diburit", title=title, msg=message, duration="short").show()
        except Exception as exc:
            print(f"[Diburit] notify (winotify) failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Open a folder in the system file manager
# ---------------------------------------------------------------------------

def open_folder(path: Path) -> None:
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["open", str(path)], check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"[Diburit] open folder failed: {exc}", file=sys.stderr)
    else:
        try:
            os.startfile(str(path))
        except Exception as exc:
            print(f"[Diburit] open folder failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Installed Hebrew TTS voices
# ---------------------------------------------------------------------------

def list_tts_voices() -> List[Dict[str, str]]:
    """Return installed Hebrew TTS voices as [{"name": ..., "id": ...}].

    macOS: queries `say -v ?` filtered to he_IL locale.
    Windows: queries SAPI5 via pyttsx3. Returns empty list on clean installs
    (no Hebrew voice pack installed) — edge-tts is the primary backend."""
    if sys.platform == "darwin":
        import re
        try:
            result = subprocess.run(
                ["say", "-v", "?"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=4,
            )
        except Exception as exc:
            print(f"[Diburit] could not list voices: {exc}", file=sys.stderr)
            return []
        voices: List[Dict[str, str]] = []
        pattern = re.compile(r"^(.+?)\s+([a-z]{2}_[A-Z]{2,4})\s+#\s*(.*)$")
        for line in (result.stdout or "").splitlines():
            m = pattern.match(line)
            if m and m.group(2) == "he_IL":
                name = m.group(1).strip()
                voices.append({"name": name, "id": name})
        voices.sort(key=lambda v: v["name"].lower())
        return voices
    else:
        voices = []
        try:
            import pyttsx3  # type: ignore
            engine = pyttsx3.init()
            for v in engine.getProperty("voices") or []:
                langs = v.languages or []
                # SAPI5 may return hex LCIDs ("040d") or BCP-47 ("he-IL")
                is_hebrew = (
                    any(
                        "he" in str(l).lower() or str(l).lower() in ("040d", "0x040d")
                        for l in langs
                    )
                    or "hebrew" in (v.name or "").lower()
                )
                if is_hebrew:
                    voices.append({"name": v.name, "id": v.id})
        except Exception:
            pass
        return voices


# ---------------------------------------------------------------------------
# Non-blocking audio playback
# ---------------------------------------------------------------------------

def play_audio_nonblocking(path: Path, volume: float, rate: float) -> None:
    """Fire-and-forget playback. Returns immediately; audio plays in background."""
    vol = max(0.0, min(volume, 1.0))
    if sys.platform == "darwin":
        r = max(0.5, min(rate, 2.5))
        try:
            subprocess.Popen(
                ["afplay", "-v", f"{vol:.2f}", "-r", f"{r:.2f}", "-q", "1", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            print(f"[Diburit] afplay failed: {exc}", file=sys.stderr)
    else:
        def _play() -> None:
            try:
                import pygame  # type: ignore
                pygame.mixer.init()
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.set_volume(vol)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    time.sleep(0.05)
            except Exception as exc:
                print(f"[Diburit] play_audio failed: {exc}", file=sys.stderr)
        threading.Thread(target=_play, daemon=True).start()


# ---------------------------------------------------------------------------
# Exclusive file lock (context manager)
# ---------------------------------------------------------------------------

def acquire_file_lock(lock_path: Path):
    """Return a context manager that holds an exclusive advisory lock."""
    if sys.platform == "darwin":
        import contextlib
        import fcntl  # type: ignore  # noqa: PLC0415

        @contextlib.contextmanager
        def _ctx():
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            except OSError as exc:
                print(f"[Diburit] lockfile open failed: {exc}", file=sys.stderr)
                yield
                return
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)

        return _ctx()
    else:
        from filelock import FileLock  # type: ignore
        return FileLock(str(lock_path), timeout=5)
