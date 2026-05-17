#!/usr/bin/env python3
"""Diburit (דיבורית) - menu-bar Hebrew dictation app for macOS.

Successor to SayHE (which itself replaced SayIt). Built as a py2app bundle
so macOS sees one stable code-signed identity instead of "Python.app", which
is what unblocked the TCC Microphone / Accessibility / AppleEvents prompts.

Pipeline:
    Cmd+Shift+M (or Start Recording in the menu)
    -> record 16 kHz mono WAV via sounddevice
    -> POST WAV to Groq Whisper-large-v3 with language=he
    -> silence-hallucination filter (drop "תודה." / "כן." / ... that Whisper
       emits on muted input)
    -> pbcopy + CGEventPost Cmd+V into whatever app is frontmost RIGHT NOW
       (deliberately late-bound so push-to-talk works correctly)
    -> write ~/Diburit/recordings/diburit_<ts>/metadata.json and atomically
       repoint the ~/Diburit/latest symlink at that directory
    -> Claude Code Stop hook picks up the metadata, classifies the reply,
       and speaks it through `say -v Carmit` + `afplay -v <volume>`.

All UI mutation flows through `main_thread_pump` (a rumps.Timer at 50 Hz)
to avoid touching AppKit from the recording / transcription / hotkey
threads.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

__version__ = "1.6.1"

import numpy as np
import rumps
import sounddevice as sd
import soundfile as sf
import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSEvent,
    NSEventMaskKeyDown,
    NSEventTypeLeftMouseUp,
    NSGridCellPlacementTrailing,
    NSGridView,
    NSMakeRect,
    NSNumberFormatter,
    NSPopUpButton,
    NSSlider,
    NSStackView,
    NSTextAlignmentRight,
    NSTextField,
    NSUserInterfaceLayoutOrientationHorizontal,
    NSUserInterfaceLayoutOrientationVertical,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject
from CoreFoundation import (
    CFMachPortCreateRunLoopSource,
    CFMachPortInvalidate,
    CFRunLoopAddSource,
    CFRunLoopGetMain,
    CFRunLoopRemoveSource,
    kCFRunLoopCommonModes,
)
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventPost,
    CGEventSetFlags,
    CGEventTapCreate,
    CGEventTapEnable,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCGEventFlagsChanged,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    kCGEventTapOptionDefault,
    kCGHIDEventTap,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

from diburit_core import (
    DIBURIT_HOME,
    DTYPE,
    EDGE_HEBREW_VOICES,
    EDGE_PREFIX,
    FOCUS_SETTLE_SEC,
    GROQ_PROMPT,
    GTTS_HEBREW_VOICES,
    GTTS_PREFIX,
    HOTKEY_MODE_PTT,
    HOTKEY_MODE_TOGGLE,
    MAX_RECORDINGS_PRESETS,
    MAX_SPEECH_RATE,
    MIN_SPEECH_RATE,
    NOTIFICATION_PREVIEW_CHARS,
    PTT_MIN_HOLD_SEC,
    QUIT_TRANSCRIBE_GRACE_SEC,
    RECORDINGS_DIR,
    SAMPLE_RATE,
    SETTINGS_FILE,
    SILENCE_PEAK_THRESHOLD,
    TRANSCRIPT_PREVIEW_CHARS,
    VOICE_LIST_TIMEOUT,
    Utterance,
    _BASE_SETTINGS,
    _atomic_write,
    _audio_is_silent,
    _is_silence_hallucination,
    _load_settings,
    _prune_recordings,
    _repoint_latest,
    _save_settings,
    _transcribe_with_groq,
)

# ---- paths & constants --------------------------------------------------

LATEST_DIR_SYMLINK = DIBURIT_HOME / "latest"

CHANNELS = 1

ICON_IDLE = "🎙"
ICON_IDLE_PTT = "🎙 ✋"
ICON_RECORDING = "🎙 🔴"
ICON_TRANSCRIBING = "🎙 …"
ICON_OFF = "🎙 ⊘"

# Tuning knobs (macOS-only or not yet moved to core).
PUMP_INTERVAL_SEC = 0.05              # rumps.Timer pump rate

DEFAULT_SETTINGS: Dict[str, object] = {**_BASE_SETTINGS, "voice": "Carmit"}

VOLUME_LEVELS: List[float] = [0.2, 0.4, 0.6, 0.8, 1.0]
# Playback speed multipliers applied via `afplay -r`. `-q 1` is passed
# alongside so the time-stretch preserves pitch instead of chipmunking
# the voice. gTTS and Edge are rendered at their natural cadence and
# only re-timed at playback — the same multiplier therefore covers all
# three backends uniformly.
SPEECH_RATE_LEVELS: List[float] = [0.9, 1.0, 1.15, 1.3, 1.5, 1.75]
_HEBREW_LOCALE = "he_IL"

# Apps we never want to paste into - includes ourselves, plus Finder which
# would rename the selected file if it gets a Cmd+V text payload.
_PASTE_BLOCKLIST = frozenset({"Diburit", "Python", "Finder", ""})

EDGE_SAMPLE_TIMEOUT_SEC = 15
GTTS_SAMPLE_TIMEOUT_SEC = 15

# Hotkey presets shown in the menubar's Hotkey submenu. Stored values use
# the pynput format (`<cmd>+<shift>+m`); labels are the macOS-style display
# form. Order is the menu order. Cmd+Shift+M is the historical default but
# collides with VS Code's "Toggle Problems" - having presets makes the
# alternative one click away.
HOTKEY_PRESETS: List[Tuple[str, str]] = [
    ("Cmd+Shift+M", "<cmd>+<shift>+m"),
    ("Cmd+Shift+;", "<cmd>+<shift>+;"),
    ("Cmd+Shift+'", "<cmd>+<shift>+'"),
    ("Cmd+Shift+/", "<cmd>+<shift>+/"),
    ("Cmd+Opt+M", "<cmd>+<alt>+m"),
    ("Ctrl+Opt+M", "<ctrl>+<alt>+m"),
    ("F13", "<f13>"),
    ("F19", "<f19>"),
]


# ---- hotkey (Quartz CGEventTap on main runloop) -------------------------
#
# pynput's GlobalHotKeys runs its event loop on a background thread and
# translates incoming events into Key/KeyCode objects via
# `TISGetInputSourceProperty`. macOS 26.3 tightened
# `dispatch_assert_queue`, and `TSMGetInputSourceProperty` now asserts
# fatally if called off the main dispatch queue. Every keypress while a
# pynput tap is active crashes the listener thread with SIGTRAP. To stay
# compatible we install our own CGEventTap on the main runloop and match
# the keycode against a hardcoded ANSI table — no layout query happens
# anywhere off the main thread.

# Apple ANSI virtual keycodes (kVK_ANSI_*). These are stable across
# US-layout keyboards and is what HIToolbox stamps on CGEvent regardless
# of the active input source for the printed character keys. Function
# keys + space have layout-independent codes by definition.
_VIRTUAL_KEYCODES: Dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0b, "q": 0x0c,
    "w": 0x0d, "e": 0x0e, "r": 0x0f, "y": 0x10, "t": 0x11,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17,
    "=": 0x18, "9": 0x19, "7": 0x1a, "-": 0x1b, "8": 0x1c, "0": 0x1d,
    "]": 0x1e, "o": 0x1f, "u": 0x20, "[": 0x21, "i": 0x22, "p": 0x23,
    "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29, "\\": 0x2a,
    ",": 0x2b, "/": 0x2c, "n": 0x2d, "m": 0x2e, ".": 0x2f, "`": 0x32,
    "space": 0x31,
    "f1": 0x7a, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6d, "f11": 0x67, "f12": 0x6f,
    "f13": 0x69, "f14": 0x6b, "f15": 0x71, "f16": 0x6a, "f17": 0x40,
    "f18": 0x4f, "f19": 0x50, "f20": 0x5a,
}
_MODIFIER_FLAGS: Dict[str, int] = {
    "cmd": kCGEventFlagMaskCommand,
    "command": kCGEventFlagMaskCommand,
    "shift": kCGEventFlagMaskShift,
    "alt": kCGEventFlagMaskAlternate,
    "option": kCGEventFlagMaskAlternate,
    "opt": kCGEventFlagMaskAlternate,
    "ctrl": kCGEventFlagMaskControl,
    "control": kCGEventFlagMaskControl,
}
_MODIFIER_MASK = (kCGEventFlagMaskCommand | kCGEventFlagMaskShift |
                  kCGEventFlagMaskAlternate | kCGEventFlagMaskControl)

# AppKit NSEvent.modifierFlags bitmasks. Pinned as integer literals so we
# don't depend on a specific pyobjc-version constant name (older builds
# expose them as ``NSCommandKeyMask``, newer as ``NSEventModifierFlagCommand``).
# Values are stable Cocoa constants defined in NSEvent.h.
_NSEVENT_FLAG_SHIFT = 1 << 17
_NSEVENT_FLAG_CONTROL = 1 << 18
_NSEVENT_FLAG_OPTION = 1 << 19
_NSEVENT_FLAG_COMMAND = 1 << 20
_NSEVENT_ANY_MODIFIER = (
    _NSEVENT_FLAG_SHIFT | _NSEVENT_FLAG_CONTROL
    | _NSEVENT_FLAG_OPTION | _NSEVENT_FLAG_COMMAND
)
# kVK_Escape — keycode for plain Escape, used to cancel hotkey capture.
_KVK_ESCAPE = 53

# Reverse lookup ``virtual keycode -> name``, built once from
# _VIRTUAL_KEYCODES. Used by the preferences-window hotkey recorder to
# translate an NSEvent.keyCode() back into the pynput-format chord string
# settings.json expects.
_KEYCODE_TO_NAME: Dict[int, str] = {code: name for name, code in _VIRTUAL_KEYCODES.items()}

# Names whose pynput-format spelling is wrapped in <angle> brackets
# (everything else, like "m" or "/", stays bare in the chord string).
_HOTKEY_ANGLE_WRAPPED = frozenset({
    "space",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10",
    "f11", "f12", "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20",
})


def _format_hotkey_from_event(keycode: int, flags: int) -> Optional[str]:
    """Convert an (NSEvent keyCode, modifierFlags) pair into the
    pynput-format chord string Diburit's settings expect, e.g.
    ``'<cmd>+<shift>+m'`` or ``'<f13>'``. Returns ``None`` if the keycode
    isn't one of the keys we have a virtual-key mapping for — the caller
    can then keep listening for the next event instead of binding to an
    unrecognised key."""
    name = _KEYCODE_TO_NAME.get(int(keycode))
    if name is None:
        return None
    parts: List[str] = []
    if flags & _NSEVENT_FLAG_COMMAND:
        parts.append("<cmd>")
    if flags & _NSEVENT_FLAG_SHIFT:
        parts.append("<shift>")
    if flags & _NSEVENT_FLAG_OPTION:
        parts.append("<alt>")
    if flags & _NSEVENT_FLAG_CONTROL:
        parts.append("<ctrl>")
    parts.append(f"<{name}>" if name in _HOTKEY_ANGLE_WRAPPED else name)
    return "+".join(parts)


def _parse_hotkey(spec: str) -> Tuple[int, int]:
    """Parse a hotkey string like '<cmd>+<shift>+m' or '<f19>' into
    (modifier_flags, virtual_keycode). Accepts the same surface syntax as
    pynput's `<mod>+<mod>+key` so settings.json values stay compatible
    after the Quartz migration. Raises ValueError on unknown tokens."""
    if not spec or not spec.strip():
        raise ValueError("empty hotkey")
    tokens = [t.strip().strip("<>").lower() for t in spec.split("+")]
    tokens = [t for t in tokens if t]
    flags = 0
    keycode: Optional[int] = None
    for t in tokens:
        if t in _MODIFIER_FLAGS:
            flags |= _MODIFIER_FLAGS[t]
        elif t in _VIRTUAL_KEYCODES:
            if keycode is not None:
                raise ValueError(f"{spec!r}: multiple non-modifier keys")
            keycode = _VIRTUAL_KEYCODES[t]
        else:
            raise ValueError(f"{spec!r}: unknown token {t!r}")
    if keycode is None:
        raise ValueError(f"{spec!r}: no key (only modifiers)")
    return flags, keycode


class _QuartzHotkey:
    """Single-binding global hotkey via CGEventTap on the main runloop.
    Replaces pynput.keyboard.GlobalHotKeys (which crashes on macOS 26.3+
    when its background listener thread queries the keyboard layout).
    The callback fires on the main thread, so menu state mutation is safe
    without going through `_main_queue`.

    With `on_released=None` we listen for KeyDown only (toggle mode).
    With a release callback we also subscribe to KeyUp + FlagsChanged so
    push-to-talk can fire on either the keycode going up *or* any of the
    required modifiers being dropped — whichever happens first. The class
    tracks `_active` to dedupe: `on_pressed` fires once per chord (no key-
    repeat spam), and `on_released` fires exactly once even if both the
    keycode and a modifier release event arrive back-to-back."""

    def __init__(
        self,
        flags: int,
        keycode: int,
        on_pressed: Callable[[], None],
        on_released: Optional[Callable[[], None]] = None,
    ) -> None:
        self._flags = flags
        self._keycode = keycode
        self._on_pressed = on_pressed
        self._on_released = on_released
        self._active = False
        self._tap = None
        self._source = None

    def start(self) -> None:
        mask = CGEventMaskBit(kCGEventKeyDown)
        if self._on_released is not None:
            # Only subscribe to up/flags events when PTT actually needs
            # them. Keeps toggle-mode taps as cheap as before.
            mask |= CGEventMaskBit(kCGEventKeyUp)
            mask |= CGEventMaskBit(kCGEventFlagsChanged)
        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            mask,
            self._callback,
            None,
        )
        if not tap:
            raise RuntimeError("CGEventTapCreate returned None (Accessibility not granted?)")
        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        self._tap = tap
        self._source = source

    def stop(self) -> None:
        tap = self._tap
        source = self._source
        self._tap = None
        self._source = None
        # If we tear the tap down mid-press, make sure the caller's release
        # callback still runs — otherwise a mode-switch or hotkey-change
        # during a held PTT chord would leave the recorder stuck on.
        was_active = self._active
        self._active = False
        if was_active and self._on_released is not None:
            try:
                self._on_released()
            except Exception as exc:
                print(f"[Diburit] hotkey release on stop raised: {exc}", file=sys.stderr)
        if tap is None:
            return
        try:
            CGEventTapEnable(tap, False)
        except Exception as exc:
            print(f"[Diburit] CGEventTapEnable(False) failed: {exc}", file=sys.stderr)
        if source is not None:
            try:
                CFRunLoopRemoveSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes)
            except Exception as exc:
                print(f"[Diburit] CFRunLoopRemoveSource failed: {exc}", file=sys.stderr)
        try:
            CFMachPortInvalidate(tap)
        except Exception as exc:
            print(f"[Diburit] CFMachPortInvalidate failed: {exc}", file=sys.stderr)

    def _fire_released(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._on_released is None:
            return
        try:
            self._on_released()
        except Exception as exc:
            print(f"[Diburit] hotkey release callback raised: {exc}", file=sys.stderr)

    def _callback(self, proxy, etype, event, refcon):
        # macOS auto-disables a tap whose callback over-runs the 1s budget
        # or that intersects with user-driven input (per CGEventTypes.h).
        # Re-enable so we don't go silent after a stall.
        if etype == kCGEventTapDisabledByTimeout or etype == kCGEventTapDisabledByUserInput:
            if self._tap is not None:
                CGEventTapEnable(self._tap, True)
            return event
        if etype == kCGEventKeyDown:
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            flags = int(CGEventGetFlags(event)) & _MODIFIER_MASK
            if keycode == self._keycode and flags == self._flags:
                # In toggle mode there's no release callback and no
                # KeyUp subscription, so `_active` would never clear and
                # the *second* toggle press would be silently suppressed.
                # Dedup only matters when we're going to fire a release.
                fire_now = self._on_released is None or not self._active
                if self._on_released is not None:
                    self._active = True
                if fire_now:
                    try:
                        self._on_pressed()
                    except Exception as exc:
                        print(f"[Diburit] hotkey callback raised: {exc}", file=sys.stderr)
                # Consume the event so the focused app (e.g. VS Code) does
                # not also receive Cmd+Shift+M and trigger its own action.
                # Also consumes key-repeat KeyDowns while held in PTT.
                return None
        elif etype == kCGEventKeyUp:
            if self._on_released is None or not self._active:
                return event
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if keycode == self._keycode:
                self._fire_released()
                # Consume the matching KeyUp so the focused app does not
                # see a lone keycode-up for a chord whose KeyDown we ate.
                return None
        elif etype == kCGEventFlagsChanged:
            if self._on_released is None or not self._active:
                return event
            flags = int(CGEventGetFlags(event)) & _MODIFIER_MASK
            # Any required modifier was dropped → release. Catches the
            # common "user lifts Cmd before letting go of M" case.
            if (flags & self._flags) != self._flags:
                self._fire_released()
        return event



def _list_hebrew_voices() -> List[Dict[str, str]]:
    # `say -v ?` emits each voice's sample line in the voice's native
    # script - Carmit's is Hebrew, so the output is UTF-8. Without an
    # explicit encoding kwarg subprocess defaults to the locale (which
    # under launchd is ascii-ish) and crashes the parser. Force UTF-8
    # and tolerate stray bytes so a single bad voice cannot blank the
    # whole menu.
    try:
        result = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=VOICE_LIST_TIMEOUT,
        )
    except Exception as exc:
        print(f"[Diburit] could not list voices: {exc}", file=sys.stderr)
        return []
    voices: List[Dict[str, str]] = []
    pattern = re.compile(r"^(.+?)\s+([a-z]{2}_[A-Z]{2,4})\s+#\s*(.*)$")
    for line in (result.stdout or "").splitlines():
        m = pattern.match(line)
        if m and m.group(2) == _HEBREW_LOCALE:
            voices.append({"name": m.group(1).strip(), "locale": m.group(2), "sample": m.group(3)})
    voices.sort(key=lambda v: v["name"].lower())
    return voices


# ---- macOS helpers ------------------------------------------------------

def _ascript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _notify(title: str, message: str) -> None:
    script = (
        f"display notification {_ascript_str(message)} "
        f"with title {_ascript_str(title)}"
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=2,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print(f"[Diburit] notification failed: {exc}", file=sys.stderr)


def _copy_to_clipboard(text: str) -> None:
    # pbcopy uses the process locale to decide the byte→text encoding.
    # When launchd spawns us, LANG is unset and pbcopy assumes MacRoman,
    # which mangles Hebrew UTF-8 (0xD7 → ◊, 0xA9 → ©, …). Force UTF-8.
    env = os.environ.copy()
    env.setdefault("LC_CTYPE", "en_US.UTF-8")
    env.setdefault("LANG", "en_US.UTF-8")
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"),
                       check=False, timeout=2, env=env)
    except Exception as exc:
        print(f"[Diburit] pbcopy failed: {exc}", file=sys.stderr)


def _frontmost_app_name() -> Optional[str]:
    script = (
        'tell application "System Events" to '
        'name of first application process whose frontmost is true'
    )
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2)
    except Exception as exc:
        print(f"[Diburit] frontmost lookup failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    name = (result.stdout or "").strip()
    return name or None


def _activate_app(name: str) -> None:
    script = f'tell application "{name}" to activate'
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=2,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print(f"[Diburit] activate {name!r} failed: {exc}", file=sys.stderr)


_V_KEY_CODE = 9  # physical V


def _send_cmd_v() -> None:
    """Cmd+V via CGEventPost. Layout-independent (key code, not character),
    and TCC attributes the event to *this* signed process, not to osascript."""
    down = CGEventCreateKeyboardEvent(None, _V_KEY_CODE, True)
    CGEventSetFlags(down, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, down)
    up = CGEventCreateKeyboardEvent(None, _V_KEY_CODE, False)
    CGEventSetFlags(up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, up)


def _paste_into_frontmost() -> Tuple[bool, str]:
    """Late-bind the paste target: look at frontmost app *now* (rather than
    at record-start). Critical for push-to-talk - by the time the hotkey
    fires, the user is already focused where they want the text to land.
    Skip if frontmost is in the blocklist (Diburit itself, Finder, etc)."""
    time.sleep(FOCUS_SETTLE_SEC)  # let any in-flight focus shift settle
    front = _frontmost_app_name()
    if not front or front in _PASTE_BLOCKLIST:
        print(f"[Diburit] paste skipped (focus was {front!r})", file=sys.stderr)
        return False, front or ""
    try:
        _send_cmd_v()
    except Exception as exc:
        print(f"[Diburit] CGEventPost failed: {exc}", file=sys.stderr)
        return False, front
    print(f"[Diburit] pasted into {front!r}", flush=True)
    return True, front



# ---- sample playback ----------------------------------------------------

def _afplay_args(path: Path, volume: float, rate: float) -> List[str]:
    """Build an afplay argv. `-q 1` enables the high-quality time-stretch
    so that rate != 1.0 preserves pitch instead of chipmunking the voice."""
    vol = max(0.0, min(volume, 1.0))
    r = max(MIN_SPEECH_RATE, min(rate, MAX_SPEECH_RATE))
    return [
        "afplay",
        "-v", f"{vol:.2f}",
        "-r", f"{r:.2f}",
        "-q", "1",
        str(path),
    ]


def _play_sample(text: str, voice: str, volume: float, rate: float = 1.0) -> None:
    """Synchronous render+play of a short Hebrew greeting for voice/volume
    preview. Subprocess.run (not Popen) so the next preview does not clobber
    a still-playing audio file. Dispatches to Edge TTS, gTTS, or `say`
    based on the prefix convention."""
    if voice.startswith(EDGE_PREFIX):
        _play_sample_edge(text, voice[len(EDGE_PREFIX):], volume, rate)
        return
    if voice.startswith(GTTS_PREFIX):
        _play_sample_gtts(text, voice[len(GTTS_PREFIX):], volume, rate)
        return

    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".aiff", prefix="diburit_sample_", dir=str(DIBURIT_HOME))
    os.close(fd)
    aiff = Path(tmp_path)
    try:
        subprocess.run(
            ["say", "-v", voice, "-o", str(aiff), text],
            check=False, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            _afplay_args(aiff, volume, rate),
            check=False, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    finally:
        try:
            aiff.unlink()
        except OSError:
            pass


def _play_sample_gtts(text: str, gtts_lang: str, volume: float, rate: float = 1.0) -> None:
    """gTTS preview path. Renders to an MP3 in DIBURIT_HOME, plays once,
    deletes. Like the Edge path, no fallback voice on failure - the point
    of the preview is to demo *this* voice."""
    try:
        from gtts import gTTS  # type: ignore
    except ImportError as exc:
        print(f"[Diburit] gtts not installed: {exc}", file=sys.stderr)
        _notify("Diburit", "gtts not installed. Run: pip install gtts")
        return

    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".mp3", prefix="diburit_sample_", dir=str(DIBURIT_HOME))
    os.close(fd)
    mp3 = Path(tmp_path)
    try:
        try:
            gTTS(text=text, lang=gtts_lang).save(str(mp3))
        except Exception as exc:
            print(f"[Diburit] gtts sample render failed: {exc}", file=sys.stderr)
            _notify("Diburit", f"gTTS failed: {str(exc)[:80]}")
            return
        if not (mp3.exists() and mp3.stat().st_size > 0):
            _notify("Diburit", "gTTS produced empty audio")
            return
        subprocess.run(
            _afplay_args(mp3, volume, rate),
            check=False, timeout=GTTS_SAMPLE_TIMEOUT_SEC,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    finally:
        try:
            mp3.unlink()
        except OSError:
            pass


def _play_sample_edge(text: str, edge_voice: str, volume: float, rate: float = 1.0) -> None:
    """Edge TTS preview path. Renders to an MP3 in DIBURIT_HOME, plays
    once, deletes. If edge_tts isn't installed or the render fails (e.g.
    no network), notifies the user — there's no fallback voice for a
    preview, since the point of the preview is to demo *this* voice."""
    try:
        import asyncio
        import edge_tts  # type: ignore
    except ImportError as exc:
        print(f"[Diburit] edge-tts not installed: {exc}", file=sys.stderr)
        _notify("Diburit", "edge-tts not installed. Run: pip install edge-tts")
        return

    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".mp3", prefix="diburit_sample_", dir=str(DIBURIT_HOME))
    os.close(fd)
    mp3 = Path(tmp_path)

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, edge_voice)
        await communicate.save(str(mp3))

    try:
        try:
            asyncio.run(asyncio.wait_for(_run(), timeout=EDGE_SAMPLE_TIMEOUT_SEC))
        except Exception as exc:
            print(f"[Diburit] edge sample render failed: {exc}", file=sys.stderr)
            _notify("Diburit", f"Edge TTS failed: {str(exc)[:80]}")
            return
        if not (mp3.exists() and mp3.stat().st_size > 0):
            _notify("Diburit", "Edge TTS produced empty audio")
            return
        subprocess.run(
            _afplay_args(mp3, volume, rate),
            check=False, timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    finally:
        try:
            mp3.unlink()
        except OSError:
            pass


# ---- preferences window -------------------------------------------------
#
# A native AppKit window built programmatically (no NIB/Storyboard) so the
# preferences are a single dialog instead of a deep submenu tree. Owns its
# controls; on user interaction, calls back into DiburitApp's existing
# apply-and-persist machinery (`_apply_hotkey`, `_apply_max_recordings`,
# `_persist_settings`). Lazy-built on first open and kept alive across
# show/close cycles so reopening is instant.

# NSGridView label-column placement constant. Right-align the label
# column so labels read as a flush column against the controls.
_GRID_TRAILING = NSGridCellPlacementTrailing


class _PrefsWindow(NSObject):
    """Owns the preferences NSWindow + all its controls. Initialise via
    ``_PrefsWindow.alloc().initWithApp_(app)`` because NSObject subclasses
    can't use Python ``__init__``."""

    def initWithApp_(self, app):
        self = objc.super(_PrefsWindow, self).init()
        if self is None:
            return None
        self._app = app
        self._window = None
        # Parallel to popup item indices: lookup voice_id by selectedIndex.
        # Built fresh each time the window opens so newly-installed system
        # voices show up without an app restart.
        self._voice_ids: List[str] = []
        # NSEvent monitor handle for the hotkey-capture flow. Non-None
        # only while the user is actively recording a new chord.
        self._key_monitor = None
        return self

    @objc.python_method
    def show(self):
        if self._window is None:
            self._build()
        self._populate()
        NSApp.activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    # ---- build -------------------------------------------------------

    @objc.python_method
    def _build(self):
        # Controls
        self._voice_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 260, 26), False
        )
        self._voice_popup.setTarget_(self)
        self._voice_popup.setAction_(b"voiceChanged:")

        self._volume_slider = self._make_slider(0.0, 1.0, b"volumeChanged:")
        self._volume_label = NSTextField.labelWithString_("  0%")

        self._speed_slider = self._make_slider(
            MIN_SPEECH_RATE, MAX_SPEECH_RATE, b"speedChanged:",
        )
        self._speed_label = NSTextField.labelWithString_("1.00x")

        # Hotkey is a click-to-record button rather than a text field —
        # typing pynput-format chord strings is unergonomic and a common
        # source of misconfiguration. Click → recordHotkeyClicked_ enters
        # capture mode, the next key combination becomes the new binding.
        self._hotkey_button = NSButton.buttonWithTitle_target_action_(
            "", self, b"recordHotkeyClicked:",
        )

        max_formatter = NSNumberFormatter.alloc().init()
        max_formatter.setAllowsFloats_(False)
        max_formatter.setMinimum_(10)
        max_formatter.setMaximum_(10_000)
        self._max_field = NSTextField.textFieldWithString_("100")
        self._max_field.setFormatter_(max_formatter)
        self._max_field.setDelegate_(self)

        self._show_status_btn = NSButton.checkboxWithTitle_target_action_(
            "Show Last & Transcript in menu", self, b"showStatusToggled:",
        )

        prune_btn = NSButton.buttonWithTitle_target_action_(
            "Prune Recordings Now", self, b"pruneClicked:",
        )

        close_btn = NSButton.buttonWithTitle_target_action_(
            "Done", self, b"closeClicked:",
        )
        close_btn.setKeyEquivalent_("\r")

        # Layout — NSGridView gives us a flush label column without
        # juggling per-row Auto Layout constraints.
        rows = [
            [self._label("Voice:"), self._voice_popup],
            [self._label("Volume:"), self._hbox([self._volume_slider, self._volume_label])],
            [self._label("Speed:"), self._hbox([self._speed_slider, self._speed_label])],
            [self._label("Hotkey:"), self._hotkey_button],
            [self._label("Max recordings:"), self._max_field],
            [self._label(""), self._show_status_btn],
            [self._label(""), prune_btn],
        ]
        grid = NSGridView.gridViewWithViews_(rows)
        grid.setRowSpacing_(10.0)
        grid.setColumnSpacing_(12.0)
        grid.columnAtIndex_(0).setXPlacement_(_GRID_TRAILING)

        main = NSStackView.stackViewWithViews_([grid, close_btn])
        main.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        main.setSpacing_(20.0)
        main.setEdgeInsets_((20.0, 24.0, 20.0, 24.0))

        # Window
        rect = NSMakeRect(0, 0, 520, 380)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False,
        )
        win.setTitle_("Diburit — Preferences")
        # Re-using the same NSWindow across open/close cycles; without
        # this, AppKit releases it on close and the next show() would
        # dereference a dead pointer.
        win.setReleasedWhenClosed_(False)
        win.setContentView_(main)
        win.center()
        self._window = win

    # ---- helpers -----------------------------------------------------

    @objc.python_method
    def _label(self, text: str):
        lbl = NSTextField.labelWithString_(text)
        lbl.setAlignment_(NSTextAlignmentRight)
        return lbl

    @objc.python_method
    def _hbox(self, views):
        s = NSStackView.stackViewWithViews_(views)
        s.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
        s.setSpacing_(8.0)
        return s

    @objc.python_method
    def _make_slider(self, lo: float, hi: float, action: bytes):
        s = NSSlider.alloc().init()
        s.setMinValue_(lo)
        s.setMaxValue_(hi)
        s.setContinuous_(True)
        s.setTarget_(self)
        s.setAction_(action)
        return s

    # ---- populate ----------------------------------------------------

    @objc.python_method
    def _populate(self):
        a = self._app

        self._voice_popup.removeAllItems()
        self._voice_ids = []

        # System Hebrew voices first
        for v in _list_hebrew_voices():
            self._voice_popup.addItemWithTitle_(v["name"])
            self._voice_ids.append(v["name"])
        # Edge neural voices
        for lbl, voice_id in EDGE_HEBREW_VOICES:
            self._voice_popup.addItemWithTitle_(lbl)
            self._voice_ids.append(voice_id)
        # gTTS
        for lbl, voice_id in GTTS_HEBREW_VOICES:
            self._voice_popup.addItemWithTitle_(lbl)
            self._voice_ids.append(voice_id)

        if a.voice in self._voice_ids:
            self._voice_popup.selectItemAtIndex_(self._voice_ids.index(a.voice))
        else:
            # Persisted voice isn't currently installed — surface it as a
            # stand-out row so the user sees the inconsistency and can
            # pick a real voice instead of looking at a silently-mismatched
            # default selection.
            self._voice_popup.addItemWithTitle_(f"{a.voice}  (not installed)")
            self._voice_ids.append(a.voice)
            self._voice_popup.selectItemAtIndex_(len(self._voice_ids) - 1)

        self._volume_slider.setDoubleValue_(a.volume)
        self._volume_label.setStringValue_(f"{int(round(a.volume * 100)):3d}%")

        self._speed_slider.setDoubleValue_(a.speech_rate)
        self._speed_label.setStringValue_(f"{a.speech_rate:.2f}x")

        self._hotkey_button.setTitle_(a.hotkey)
        self._max_field.setStringValue_(str(a.max_recordings_kept))
        self._show_status_btn.setState_(1 if a.show_status_rows else 0)

    # ---- actions -----------------------------------------------------

    def voiceChanged_(self, sender):
        idx = sender.indexOfSelectedItem()
        if not (0 <= idx < len(self._voice_ids)):
            return
        voice_id = self._voice_ids[idx]
        if voice_id == self._app.voice:
            return
        self._app.voice = voice_id
        self._app._persist_settings()
        # Preview the new voice off-main so the click stays responsive.
        threading.Thread(
            target=_play_sample,
            args=("שלום, איך הולך?", self._app.voice, self._app.volume, self._app.speech_rate),
            daemon=True,
        ).start()

    def volumeChanged_(self, sender):
        v = float(sender.doubleValue())
        # Round to one of the legacy preset levels so the persisted value
        # stays predictable and matches what the menu used to offer.
        v = max(0.0, min(v, 1.0))
        self._app.volume = v
        self._app._persist_settings()
        self._volume_label.setStringValue_(f"{int(round(v * 100)):3d}%")

    def speedChanged_(self, sender):
        # Slider returns full-precision doubles (e.g. 1.23456789) which
        # then leak into both the label and settings.json. Quantize to
        # 2 decimals so the display stays tidy and the on-disk value
        # round-trips cleanly across reads.
        v = round(float(sender.doubleValue()), 2)
        v = max(MIN_SPEECH_RATE, min(v, MAX_SPEECH_RATE))
        self._app.speech_rate = v
        self._app._persist_settings()
        self._speed_label.setStringValue_(f"{v:.2f}x")
        # Play a sample only when the user releases the slider, not on
        # every continuous-tracking tick. Without the mouseUp gate the
        # drag would queue up dozens of overlapping `say` invocations.
        event = NSApp.currentEvent()
        if event is not None and event.type() == NSEventTypeLeftMouseUp:
            threading.Thread(
                target=_play_sample,
                args=("שלום, איך הולך?", self._app.voice, self._app.volume, self._app.speech_rate),
                daemon=True,
            ).start()

    def showStatusToggled_(self, sender):
        self._app.show_status_rows = bool(sender.state())
        self._app._persist_settings()
        self._app._refresh_menu()

    def pruneClicked_(self, _sender):
        self._app.on_prune_now(None)

    def closeClicked_(self, _sender):
        # Closing the window while capture is mid-flight would leave the
        # NSEvent monitor live and the global hotkey listener torn down —
        # i.e. all key events would silently get consumed app-wide.
        if self._key_monitor is not None:
            self._stop_hotkey_capture()
        self._window.orderOut_(None)

    # ---- hotkey capture ---------------------------------------------

    def recordHotkeyClicked_(self, _sender):
        """Toggle hotkey-capture mode. While capturing, the global tap is
        torn down so it doesn't swallow the chord before our local NSEvent
        monitor sees it. A second click on the button (or Esc) cancels."""
        if self._key_monitor is not None:
            self._stop_hotkey_capture()
            return
        self._hotkey_button.setTitle_("Press a key combination… (Esc to cancel)")
        self._app._uninstall_hotkey_listener()

        def handler(event):
            keycode = int(event.keyCode())
            flags = int(event.modifierFlags())
            # Plain Escape cancels without binding.
            if keycode == _KVK_ESCAPE and not (flags & _NSEVENT_ANY_MODIFIER):
                self._stop_hotkey_capture()
                return None
            chord = _format_hotkey_from_event(keycode, flags)
            if chord is None:
                # Unrecognised keycode — keep listening so the user can
                # try again instead of binding to something that won't
                # parse later.
                return None
            self._stop_hotkey_capture()
            self._app._apply_hotkey(chord)
            self._hotkey_button.setTitle_(self._app.hotkey)
            return None

        self._key_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, handler,
        )

    @objc.python_method
    def _stop_hotkey_capture(self):
        if self._key_monitor is not None:
            NSEvent.removeMonitor_(self._key_monitor)
            self._key_monitor = None
        # Always reinstall the global hotkey, even if capture was
        # cancelled, so the user is never left without a working chord.
        self._app._install_hotkey_listener()
        self._hotkey_button.setTitle_(self._app.hotkey)

    # ---- NSTextFieldDelegate ----------------------------------------

    def controlTextDidEndEditing_(self, notif):
        """Max-recordings field routes here. Apply-on-end-editing so the
        user gets clamping/rollback feedback the moment they Tab or
        Return out of the field, not later on Done."""
        field = notif.object()
        if field is self._max_field:
            try:
                n = int(field.stringValue())
            except ValueError:
                field.setStringValue_(str(self._app.max_recordings_kept))
                return
            self._app._apply_max_recordings(n)
            # Clamped value may differ from typed value.
            field.setStringValue_(str(self._app.max_recordings_kept))


# ---- app ----------------------------------------------------------------

# Actions enqueued from background threads (recording / transcription /
# hotkey) for the main thread to apply. Strings, not enums, so the queue
# stays trivially serializable for debugging.
_ACTION_TOGGLE_RECORDING = "toggle_recording"
_ACTION_START_RECORDING = "start_recording"
_ACTION_STOP_RECORDING = "stop_recording"
_ACTION_REFRESH_MENU = "refresh_menu"


class DiburitApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(ICON_IDLE, quit_button=None)

        settings = _load_settings(defaults=DEFAULT_SETTINGS)
        self.voice: str = str(settings.get("voice", DEFAULT_SETTINGS["voice"]))
        self.volume: float = float(settings.get("volume", DEFAULT_SETTINGS["volume"]))  # type: ignore[arg-type]
        self.hotkey: str = str(settings.get("hotkey", DEFAULT_SETTINGS["hotkey"]))
        self.hotkey_mode: str = str(settings.get("hotkey_mode", DEFAULT_SETTINGS["hotkey_mode"]))
        self.max_recordings_kept: int = int(settings.get("max_recordings_kept", 100))  # type: ignore[arg-type]
        self.speech_rate: float = float(settings.get("speech_rate", DEFAULT_SETTINGS["speech_rate"]))  # type: ignore[arg-type]
        self.show_status_rows: bool = bool(settings.get("show_status_rows", DEFAULT_SETTINGS["show_status_rows"]))

        # Live state - all mutated only from the main thread.
        self.enabled: bool = True
        self.recording: bool = False
        self.transcribing: bool = False
        self._buffer: List[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._record_started_at: float = 0.0
        self._current_utterance: Optional[Utterance] = None
        self.last_recording_path: Optional[str] = None
        self.last_transcript: Optional[str] = None

        # Cross-thread queue for everything else.
        self._main_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()

        # Build menu.
        self.toggle_item = rumps.MenuItem("Status: ON", callback=self.on_toggle_enabled)
        self.record_item = rumps.MenuItem(f"Start Recording   ({self.hotkey})",
                                          callback=self.on_toggle_recording)
        self.last_item = rumps.MenuItem("Last recording: (none)", callback=None)
        self.transcript_item = rumps.MenuItem("Transcript: (none)", callback=None)
        self.quit_item = rumps.MenuItem("Quit Diburit", callback=self.on_quit)

        self.ptt_item = rumps.MenuItem("Push-to-Talk Mode", callback=self.on_toggle_ptt_mode)
        self.ptt_item.state = 1 if self.hotkey_mode == HOTKEY_MODE_PTT else 0
        self.open_folder_item = rumps.MenuItem("Open Diburit Folder…", callback=self.on_open_folder)
        self.preferences_item = rumps.MenuItem("Preferences…", callback=self.on_open_preferences)

        # Preferences (voice / volume / speed / hotkey / max recordings /
        # show-status / prune-now) live in a single native window opened
        # by ``Preferences…`` — see _PrefsWindow. Lazy-built so the AppKit
        # heavyweights aren't allocated until the user actually opens it.
        self._prefs_window: Optional[_PrefsWindow] = None

        self.menu = [
            self.toggle_item,
            None,
            self.record_item,
            self.ptt_item,
            self.last_item,
            self.transcript_item,
            None,
            self.preferences_item,
            self.open_folder_item,
            None,
            self.quit_item,
        ]

        self._refresh_menu()

        # Start the global hotkey listener. CGEventTap runs on the main
        # runloop; this attribute holds the wrapper so we can stop/replace
        # it on hotkey or mode change.
        self._hotkey_listener: Optional[_QuartzHotkey] = None
        self._install_hotkey_listener()

        # Pump cross-thread actions onto the main thread.
        rumps.Timer(self._pump_main_queue, PUMP_INTERVAL_SEC).start()

    def _persist_settings(self) -> None:
        """Snapshot the current live state into settings.json. Single point
        of truth so any new setting only has to be added once (vs. updating
        every callback that mutates a field)."""
        _save_settings({
            "voice": self.voice,
            "volume": self.volume,
            "hotkey": self.hotkey,
            "hotkey_mode": self.hotkey_mode,
            "max_recordings_kept": self.max_recordings_kept,
            "speech_rate": self.speech_rate,
            "show_status_rows": self.show_status_rows,
        })

    # ---- menu rendering -----------------------------------------------

    def _refresh_menu(self) -> None:
        self.toggle_item.title = f"Status: {'ON' if self.enabled else 'OFF'}"
        if self.recording:
            record_label = "Stop Recording"
        elif self.hotkey_mode == HOTKEY_MODE_PTT:
            record_label = "Hold to Record"
        else:
            record_label = "Start Recording"
        self.record_item.title = f"{record_label}   ({self.hotkey})"
        # Record item is always callback-enabled so the hotkey-toggle works
        # even when transcribing - the start path bails internally if busy.
        self.record_item.set_callback(self.on_toggle_recording if self.enabled else None)

        if self.recording:
            self.title = ICON_RECORDING
        elif self.transcribing:
            self.title = ICON_TRANSCRIBING
        elif not self.enabled:
            self.title = ICON_OFF
        elif self.hotkey_mode == HOTKEY_MODE_PTT:
            self.title = ICON_IDLE_PTT
        else:
            self.title = ICON_IDLE

        if self.last_recording_path:
            self.last_item.title = f"Last: {os.path.basename(os.path.dirname(self.last_recording_path))}"
        else:
            self.last_item.title = "Last recording: (none)"

        if self.transcribing:
            self.transcript_item.title = "Transcript: transcribing..."
        elif self.last_transcript:
            preview = self.last_transcript
            if len(preview) > TRANSCRIPT_PREVIEW_CHARS:
                preview = preview[: TRANSCRIPT_PREVIEW_CHARS - 3] + "..."
            self.transcript_item.title = f"Transcript: {preview}"
        else:
            self.transcript_item.title = "Transcript: (none)"

        # Last + Transcript are informational only; hide both when the
        # user has turned the rows off via the Settings toggle. Driven
        # by .hidden (NSMenuItem.setHidden_) so the rows collapse out of
        # the menu rather than just going blank.
        self.last_item.hidden = not self.show_status_rows
        self.transcript_item.hidden = not self.show_status_rows

    # ---- main-thread pump --------------------------------------------

    def _pump_main_queue(self, _timer) -> None:
        while True:
            try:
                action, payload = self._main_queue.get_nowait()
            except queue.Empty:
                return
            if action == _ACTION_TOGGLE_RECORDING:
                self.on_toggle_recording(None)
            elif action == _ACTION_START_RECORDING:
                if self.enabled and not self.recording:
                    self._start_recording()
            elif action == _ACTION_STOP_RECORDING:
                if self.recording:
                    self._stop_recording()
            elif action == _ACTION_REFRESH_MENU:
                if isinstance(payload, dict):
                    for k, v in payload.items():
                        setattr(self, k, v)
                self._refresh_menu()

    def _enqueue_refresh(self, **state_updates) -> None:
        """Call from any thread to update state + refresh menu on main."""
        self._main_queue.put((_ACTION_REFRESH_MENU, state_updates))

    # ---- hotkey -------------------------------------------------------

    def _install_hotkey_listener(self) -> None:
        """(Re)install the global hotkey listener for self.hotkey. On any
        failure self._hotkey_listener is left as None so callers can detect
        the failure and (e.g.) roll back to the previous hotkey value.

        In PTT mode we register a release callback too; in toggle mode we
        pass `on_released=None` so the tap doesn't even subscribe to
        KeyUp/FlagsChanged events."""
        self._hotkey_listener = None
        if not self.hotkey:
            return
        try:
            flags, keycode = _parse_hotkey(self.hotkey)
        except Exception as exc:
            print(f"[Diburit] hotkey parse failed: {exc}", file=sys.stderr)
            return
        if self.hotkey_mode == HOTKEY_MODE_PTT:
            listener = _QuartzHotkey(
                flags, keycode,
                on_pressed=self._on_hotkey_ptt_pressed,
                on_released=self._on_hotkey_ptt_released,
            )
        else:
            listener = _QuartzHotkey(flags, keycode, on_pressed=self._on_hotkey_toggle)
        try:
            listener.start()
        except Exception as exc:
            print(f"[Diburit] hotkey listener failed: {exc}", file=sys.stderr)
            return
        self._hotkey_listener = listener
        print(f"[Diburit] hotkey listener active: {self.hotkey} ({self.hotkey_mode})", flush=True)

    def _uninstall_hotkey_listener(self) -> None:
        listener = self._hotkey_listener
        self._hotkey_listener = None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception as exc:
            print(f"[Diburit] error stopping hotkey listener: {exc}", file=sys.stderr)

    def _apply_hotkey(self, candidate: str) -> None:
        """Validate, swap the listener, persist, refresh menu. Rolls back
        to the previous hotkey if the new one cannot be parsed or
        registered (e.g. the chord is already claimed system-wide)."""
        candidate = (candidate or "").strip()
        if not candidate or candidate == self.hotkey:
            return
        try:
            _parse_hotkey(candidate)
        except Exception as exc:
            _notify("Diburit", f"Invalid hotkey: {str(exc)[:120]}")
            return

        old_hotkey = self.hotkey
        self._uninstall_hotkey_listener()
        self.hotkey = candidate
        self._install_hotkey_listener()
        if self._hotkey_listener is None:
            # Could not register the new chord - restore the old one so
            # the user is never left without any working shortcut.
            _notify("Diburit", f"Could not register {candidate}")
            self.hotkey = old_hotkey
            self._install_hotkey_listener()
            self._refresh_menu()
            return

        self._persist_settings()
        self._refresh_menu()
        _notify("Diburit", f"Hotkey: {candidate}")

    def _on_hotkey_toggle(self) -> None:
        # The Quartz CGEventTap fires on the main thread (we installed it
        # on the main runloop), so a direct call would be safe — but we
        # marshal through `_main_queue` anyway so the toggle stays a quick
        # enqueue and doesn't risk the tap's ~1s callback budget if
        # _start_recording's sounddevice setup happens to stall.
        self._main_queue.put((_ACTION_TOGGLE_RECORDING, None))

    def _on_hotkey_ptt_pressed(self) -> None:
        # Same marshalling rationale as the toggle path: sounddevice
        # InputStream.start() can block for a few hundred ms when the mic
        # device is asleep, which would blow the tap's 1s budget.
        self._main_queue.put((_ACTION_START_RECORDING, None))

    def _on_hotkey_ptt_released(self) -> None:
        self._main_queue.put((_ACTION_STOP_RECORDING, None))

    # ---- menu callbacks -----------------------------------------------

    def on_toggle_enabled(self, _sender) -> None:
        if self.recording:
            self._stop_recording()
        self.enabled = not self.enabled
        self._refresh_menu()
        _notify("Diburit", "Enabled" if self.enabled else "Disabled")

    def on_toggle_recording(self, _sender) -> None:
        if not self.enabled:
            _notify("Diburit", "Disabled - toggle Status to ON first")
            return
        if self.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def on_quit(self, _sender) -> None:
        if self.recording:
            self._stop_recording()
        # Best-effort: give the in-flight transcription a moment to finish.
        if self.transcribing:
            _notify("Diburit", "Finishing transcription...")
            deadline = time.monotonic() + QUIT_TRANSCRIBE_GRACE_SEC
            while self.transcribing and time.monotonic() < deadline:
                time.sleep(0.1)
        rumps.quit_application()

    def on_toggle_ptt_mode(self, _sender) -> None:
        """Flip between toggle and push-to-talk. If a recording is in
        flight (started via the previous mode), we end it cleanly before
        swapping the listener — otherwise the listener tear-down would
        already fire `on_released` and stop it for us, but doing it
        explicitly keeps the order deterministic."""
        if self.recording:
            self._stop_recording()
        new_mode = HOTKEY_MODE_TOGGLE if self.hotkey_mode == HOTKEY_MODE_PTT else HOTKEY_MODE_PTT
        self._uninstall_hotkey_listener()
        self.hotkey_mode = new_mode
        self._install_hotkey_listener()
        if self._hotkey_listener is None:
            # Listener registration failed for the new mode. Roll back so
            # the user is never left without a working hotkey.
            print(f"[Diburit] PTT mode swap failed; rolling back to "
                  f"{HOTKEY_MODE_TOGGLE if new_mode == HOTKEY_MODE_PTT else HOTKEY_MODE_PTT}",
                  file=sys.stderr)
            self.hotkey_mode = HOTKEY_MODE_TOGGLE if new_mode == HOTKEY_MODE_PTT else HOTKEY_MODE_PTT
            self._install_hotkey_listener()
            _notify("Diburit", "Could not switch hotkey mode")
            self.ptt_item.state = 1 if self.hotkey_mode == HOTKEY_MODE_PTT else 0
            self._refresh_menu()
            return
        self.ptt_item.state = 1 if self.hotkey_mode == HOTKEY_MODE_PTT else 0
        self._persist_settings()
        self._refresh_menu()
        _notify(
            "Diburit",
            "Push-to-Talk: hold the hotkey to record"
            if self.hotkey_mode == HOTKEY_MODE_PTT
            else "Toggle: press to start, press again to stop",
        )

    def _apply_max_recordings(self, n: int) -> None:
        """Validate and persist a max-recordings change. Same clamp as
        ``_load_settings`` so a value entered through the prefs window
        can't bypass the [10, 10_000] guardrail that the on-disk loader
        enforces."""
        n = max(10, min(int(n), 10_000))
        if n == self.max_recordings_kept:
            return
        self.max_recordings_kept = n
        self._persist_settings()

    def on_prune_now(self, _sender) -> None:
        """Manually trigger the same prune that runs after each successful
        transcription. Useful after lowering the keep-count, since the
        post-transcription prune only fires on the next recording."""
        def _do_prune() -> None:
            _prune_recordings(self.max_recordings_kept)
            _notify("Diburit", f"Pruned recordings (keeping {self.max_recordings_kept})")
        threading.Thread(target=_do_prune, daemon=True).start()

    def on_open_folder(self, _sender) -> None:
        try:
            subprocess.run(["open", str(DIBURIT_HOME)], check=False, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            print(f"[Diburit] open folder failed: {exc}", file=sys.stderr)
            _notify("Diburit", f"Could not open folder: {exc}")

    def on_open_preferences(self, _sender) -> None:
        if self._prefs_window is None:
            self._prefs_window = _PrefsWindow.alloc().initWithApp_(self)
        self._prefs_window.show()

    # ---- recording ---------------------------------------------------

    def _start_recording(self) -> None:
        if self.transcribing:
            _notify("Diburit", "Still transcribing previous recording")
            return
        self._buffer = []
        self._current_utterance = Utterance.fresh()

        def _callback(indata, _frames, _time_info, status) -> None:
            if status:
                print(f"[Diburit] stream status: {status}", file=sys.stderr)
            self._buffer.append(indata.copy())

        stream: Optional[sd.InputStream] = None
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE, callback=_callback,
            )
            stream.start()
        except Exception as exc:
            print(f"[Diburit] failed to start stream: {exc}", file=sys.stderr)
            _notify("Diburit", f"Could not start: {exc}")
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            self._stream = None
            return

        self._stream = stream
        self.recording = True
        self._record_started_at = time.monotonic()
        self._refresh_menu()
        _notify("Diburit", "Recording...")
        print("[Diburit] recording started", flush=True)

    def _stop_recording(self) -> None:
        if not self.recording:
            return
        self.recording = False
        duration = time.monotonic() - self._record_started_at

        if self._stream is not None:
            # `abort()` (Pa_AbortStream) instead of `stop()` (Pa_StopStream)
            # because the latter waits for the CoreAudio HAL IO-proc mutex
            # and deadlocks the main runloop under macOS 26.x with certain
            # input devices — observed live as a `AudioOutputUnitStop ->
            # HALB_Mutex::Lock` stall on macOS 26.5. abort skips the drain,
            # which is fine since the audio is already captured in
            # `self._buffer` via the callback.
            try:
                self._stream.abort()
            except Exception as exc:
                print(f"[Diburit] error aborting stream: {exc}", file=sys.stderr)
            try:
                self._stream.close()
            except Exception as exc:
                print(f"[Diburit] error closing stream: {exc}", file=sys.stderr)
            self._stream = None

        utterance = self._current_utterance
        self._current_utterance = None

        if not self._buffer or utterance is None:
            print("[Diburit] stopped with empty buffer", flush=True)
            _notify("Diburit", "Stopped (no audio captured)")
            self._refresh_menu()
            return

        if self.hotkey_mode == HOTKEY_MODE_PTT and duration < PTT_MIN_HOLD_SEC:
            # Treat a sub-threshold PTT hold as an accidental tap. The
            # buffer is dropped before transcription so we don't spend a
            # Groq call on ~150ms of room tone.
            print(f"[Diburit] dropped PTT tap ({duration*1000:.0f}ms < "
                  f"{PTT_MIN_HOLD_SEC*1000:.0f}ms)", flush=True)
            self._buffer = []
            self._refresh_menu()
            return

        data = np.concatenate(self._buffer, axis=0)
        self._buffer = []

        if _audio_is_silent(data):
            print(f"[Diburit] dropped silent {duration:.1f}s recording (peak below threshold)", flush=True)
            _notify("Diburit", "⚠ Mic is silent - check System Settings > Sound > Input")
            self._refresh_menu()
            return

        sf.write(str(utterance.audio_path), data, SAMPLE_RATE, subtype="PCM_16")
        self.last_recording_path = str(utterance.audio_path)
        print(f"[Diburit] recorded {duration:.1f}s -> {utterance.audio_path}", flush=True)
        _notify("Diburit", f"Recorded {duration:.1f}s, transcribing...")
        self._start_transcription(utterance)
        self._refresh_menu()

    # ---- transcription -----------------------------------------------

    def _start_transcription(self, utterance: Utterance) -> None:
        self.transcribing = True
        self._refresh_menu()
        threading.Thread(
            target=self._transcribe_worker, args=(utterance,), daemon=True,
        ).start()

    def _transcribe_worker(self, utterance: Utterance) -> None:
        try:
            text = _transcribe_with_groq(utterance.audio_path)
        except Exception as exc:
            print(f"[Diburit] transcription failed: {exc}", file=sys.stderr)
            _notify("Diburit", f"Transcription failed: {str(exc)[:120]}")
            self._enqueue_refresh(transcribing=False)
            return

        if _is_silence_hallucination(text):
            print(f"[Diburit] dropped silence hallucination {text!r}", flush=True)
            _notify("Diburit", "⚠ Silent recording (Whisper hallucination filtered)")
            self._enqueue_refresh(transcribing=False)
            return

        utterance.transcript = text
        try:
            _atomic_write(utterance.transcript_path, text + "\n")
        except OSError as exc:
            print(f"[Diburit] could not write transcript: {exc}", file=sys.stderr)

        _copy_to_clipboard(text)
        pasted, target_app = _paste_into_frontmost()
        utterance.target_app = target_app
        if pasted:
            utterance.pasted_at = time.time()

        try:
            utterance.write_metadata()
            _repoint_latest(utterance.dir)
        except OSError as exc:
            print(f"[Diburit] could not finalize utterance metadata: {exc}", file=sys.stderr)

        preview = text if len(text) <= NOTIFICATION_PREVIEW_CHARS else text[: NOTIFICATION_PREVIEW_CHARS - 3] + "..."
        print(f"[Diburit] transcript: {text}", flush=True)
        if pasted:
            prefix = f"📋 {target_app}: "
        elif target_app:
            prefix = f"⚠ paste skipped ({target_app}, on clipboard): "
        else:
            prefix = "⚠ paste failed (clipboard only): "
        _notify("Diburit", prefix + preview)

        self._enqueue_refresh(transcribing=False, last_transcript=text)

        _prune_recordings(self.max_recordings_kept)


def _redirect_io_to_runtime_log() -> None:
    """Reroute stdout/stderr to ~/Diburit/runtime.log. When the LaunchAgent
    launches us via `open -W`, the plist's StandardOutPath / StandardErrorPath
    capture only `open`'s file descriptors, not Diburit's — so without this,
    every `print()` and Python traceback inside the app is lost. We do this
    very early in main() so even import-time chatter is captured."""
    log_path = DIBURIT_HOME / "runtime.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "a", buffering=1, encoding="utf-8")
    os.dup2(fh.fileno(), 1)
    os.dup2(fh.fileno(), 2)


def main() -> None:
    DIBURIT_HOME.mkdir(parents=True, exist_ok=True)
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    _redirect_io_to_runtime_log()
    print(f"[Diburit] === starting v{__version__} pid={os.getpid()} ===", flush=True)
    DiburitApp().run()


if __name__ == "__main__":
    main()
