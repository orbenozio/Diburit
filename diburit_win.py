#!/usr/bin/env python3
"""Diburit (דיבורית) — Windows system-tray Hebrew dictation app.

Same pipeline as diburit.py (macOS) but uses:
  - pystray   instead of rumps        (system tray)
  - tkinter   instead of AppKit       (preferences window)
  - pynput    instead of CGEventTap   (global hotkey)
  - platform_compat for clipboard, paste, notifications, audio

Run with pythonw.exe to suppress the console window:
    .venv\\Scripts\\pythonw.exe diburit_win.py
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple

import numpy as np
import sounddevice as sd
import soundfile as sf
from PIL import Image, ImageDraw
from pynput import keyboard as kb

import platform_compat
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

__version__ = "1.7.1"

DEFAULT_SETTINGS: Dict[str, object] = {**_BASE_SETTINGS, "voice": "edge:he-IL-HilaNeural"}

CHANNELS = 1

PUMP_INTERVAL_MS = 50   # tkinter root.after interval

# ---------------------------------------------------------------------------
# Paste blocklist (Windows-specific)
# ---------------------------------------------------------------------------

_PASTE_BLOCKLIST = frozenset({
    "Diburit",
    "cmd", "powershell", "WindowsTerminal",
    "",
})

# ---------------------------------------------------------------------------
# Tray icon states
# ---------------------------------------------------------------------------

_ICON_COLORS = {
    "idle":         (138, 43, 226, 255),   # violet
    "idle_ptt":     (0,   120, 215, 255),  # blue
    "recording":    (220,  20,  60, 255),  # crimson
    "transcribing": (255, 140,   0, 255),  # orange
    "disabled":     (128, 128, 128, 255),  # grey
}
_ICON_SIZE = 64


def _make_icon(state: str = "idle") -> Image.Image:
    color = _ICON_COLORS.get(state, _ICON_COLORS["idle"])
    img  = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    m = 6
    draw.ellipse([m, m, _ICON_SIZE - m, _ICON_SIZE - m], fill=color)
    # Microphone body (white rectangle)
    cx = _ICON_SIZE // 2
    draw.rounded_rectangle([cx - 7, 14, cx + 7, 36], radius=6, fill=(255, 255, 255, 220))
    # Stand
    draw.arc([cx - 12, 28, cx + 12, 48], start=0, end=180, fill=(255, 255, 255, 200), width=3)
    draw.line([cx, 48, cx, 54], fill=(255, 255, 255, 200), width=3)
    draw.line([cx - 6, 54, cx + 6, 54], fill=(255, 255, 255, 200), width=3)
    return img


# ---------------------------------------------------------------------------
# Hotkey helpers
# ---------------------------------------------------------------------------

_PYNPUT_MOD_MAP: Dict[str, kb.Key] = {
    "cmd":     kb.Key.ctrl,   # <cmd> on disk → Ctrl on Windows
    "command": kb.Key.ctrl,
    "ctrl":    kb.Key.ctrl,
    "control": kb.Key.ctrl,
    "shift":   kb.Key.shift,
    "alt":     kb.Key.alt,
    "option":  kb.Key.alt,
    "opt":     kb.Key.alt,
}

# pynput reports ctrl_l/ctrl_r/shift_l/shift_r — normalise to generic form
_KEY_NORMALISE: Dict[kb.Key, kb.Key] = {
    kb.Key.ctrl_l:  kb.Key.ctrl,
    kb.Key.ctrl_r:  kb.Key.ctrl,
    kb.Key.shift_l: kb.Key.shift,
    kb.Key.shift_r: kb.Key.shift,
    kb.Key.alt_l:   kb.Key.alt,
    kb.Key.alt_r:   kb.Key.alt,
    kb.Key.alt_gr:  kb.Key.alt,
}


def _norm(key: object) -> object:
    """Normalise key to a canonical form for chord matching."""
    if isinstance(key, kb.Key):
        return _KEY_NORMALISE.get(key, key)
    if isinstance(key, kb.KeyCode) and key.char is not None:
        return kb.KeyCode.from_char(key.char.lower())
    return key

_PYNPUT_FKEY_MAP: Dict[str, kb.Key] = {
    f"f{n}": getattr(kb.Key, f"f{n}", kb.Key.f1) for n in range(1, 21)
}


def _parse_pynput_chord(spec: str) -> FrozenSet:
    """Parse '<cmd>+<shift>+m' into a frozenset of pynput key objects.
    <cmd> is mapped to Ctrl on Windows."""
    tokens = [t.strip().strip("<>").lower() for t in spec.split("+") if t.strip()]
    keys: Set = set()
    for t in tokens:
        if t in _PYNPUT_MOD_MAP:
            keys.add(_PYNPUT_MOD_MAP[t])
        elif t in _PYNPUT_FKEY_MAP:
            keys.add(_PYNPUT_FKEY_MAP[t])
        elif len(t) == 1:
            keys.add(kb.KeyCode.from_char(t))
    return frozenset(keys)


# ---------------------------------------------------------------------------
# Win32 virtual-key codes for low-level event suppression
# ---------------------------------------------------------------------------
# Each modifier maps to a *group* of VK codes (generic + left + right variants).
# A chord is satisfied when at least one VK from each required group is held.
_VK_MOD_GROUPS: Dict[str, FrozenSet[int]] = {
    "ctrl":  frozenset({0x11, 0xA2, 0xA3}),  # VK_CONTROL, VK_LCONTROL, VK_RCONTROL
    "shift": frozenset({0x10, 0xA0, 0xA1}),
    "alt":   frozenset({0x12, 0xA4, 0xA5}),
}
_VK_FKEY_MAP: Dict[str, int] = {f"f{n}": 0x6F + n for n in range(1, 25)}  # F1=0x70…F24=0x87


def _parse_vk_chord(spec: str) -> Tuple[List[FrozenSet[int]], FrozenSet[int]]:
    """Parse a hotkey spec into ([modifier groups], leaf VKs).
    Modifiers map to <cmd>/<ctrl>=ctrl group, <shift>=shift group, <alt>=alt group.
    Leaf is the non-modifier key (letter / digit / function key)."""
    tokens = [t.strip().strip("<>").lower() for t in spec.split("+") if t.strip()]
    mod_groups: List[FrozenSet[int]] = []
    leaf_vks: Set[int] = set()
    for t in tokens:
        if t in {"cmd", "command", "ctrl", "control"}:
            mod_groups.append(_VK_MOD_GROUPS["ctrl"])
        elif t == "shift":
            mod_groups.append(_VK_MOD_GROUPS["shift"])
        elif t in {"alt", "option", "opt"}:
            mod_groups.append(_VK_MOD_GROUPS["alt"])
        elif t in _VK_FKEY_MAP:
            leaf_vks.add(_VK_FKEY_MAP[t])
        elif len(t) == 1 and t.isalpha():
            leaf_vks.add(ord(t.upper()))
        elif len(t) == 1 and t.isdigit():
            leaf_vks.add(ord(t))
    return mod_groups, frozenset(leaf_vks)


def _make_event_filter(listener_ref: List[object], spec: str):
    """Build a win32_event_filter that suppresses the hotkey from reaching
    other applications, while letting modifiers pass through normally so
    Ctrl/Shift/Alt still work everywhere else."""
    mod_groups, leaf_vks = _parse_vk_chord(spec)
    held_modifiers: Set[int] = set()
    all_mod_vks: Set[int] = set().union(*mod_groups) if mod_groups else set()

    def _modifiers_satisfied() -> bool:
        return all(bool(group & held_modifiers) for group in mod_groups)

    def _filter(msg: int, data: object) -> None:
        # msg: 0x100 WM_KEYDOWN, 0x101 WM_KEYUP, 0x104 WM_SYSKEYDOWN, 0x105 WM_SYSKEYUP
        vk = int(getattr(data, "vkCode", 0))
        is_press   = msg in (0x100, 0x104)
        is_release = msg in (0x101, 0x105)
        if vk in all_mod_vks:
            if is_press:
                held_modifiers.add(vk)
            elif is_release:
                held_modifiers.discard(vk)
            return
        if vk in leaf_vks and _modifiers_satisfied():
            listener = listener_ref[0] if listener_ref else None
            if listener is not None:
                try:
                    listener.suppress_event()  # type: ignore[attr-defined]
                except Exception:
                    pass

    return _filter


def _hotkey_to_pynput_str(spec: str) -> str:
    """Convert stored macOS form ('<cmd>+<shift>+m') to pynput GlobalHotKeys
    format ('<ctrl>+<shift>+m'). Only used for toggle mode."""
    return (
        spec
        .replace("<cmd>", "<ctrl>")
        .replace("<command>", "<ctrl>")
    )


def _hotkey_display(spec: str) -> str:
    """Convert stored hotkey spec to a human-readable Windows label."""
    return (
        spec
        .replace("<cmd>",     "Ctrl")
        .replace("<command>", "Ctrl")
        .replace("<ctrl>",    "Ctrl")
        .replace("<shift>",   "Shift")
        .replace("<alt>",     "Alt")
        .replace("<option>",  "Alt")
        .replace("<opt>",     "Alt")
        .replace("+",         "+")
    )


# ---------------------------------------------------------------------------
# Voice preview (async, non-blocking)
# ---------------------------------------------------------------------------

def _play_sample(text: str, voice: str, volume: float, rate: float = 1.0) -> None:
    """Play a short TTS sample. Dispatched to a daemon thread by callers."""
    import asyncio
    import tempfile as _tempfile

    if voice.startswith(EDGE_PREFIX):
        edge_voice = voice[len(EDGE_PREFIX):]
        try:
            import edge_tts  # type: ignore
        except ImportError:
            platform_compat.notify("Diburit", "edge-tts not installed. Run: pip install edge-tts")
            return
        tmp_mp3 = Path(_tempfile.mktemp(suffix=".mp3", dir=str(DIBURIT_HOME)))
        async def _run() -> None:
            await edge_tts.Communicate(text, edge_voice).save(str(tmp_mp3))
        try:
            asyncio.run(asyncio.wait_for(_run(), timeout=15))
            if tmp_mp3.exists() and tmp_mp3.stat().st_size > 0:
                platform_compat.play_audio_nonblocking(tmp_mp3, volume, rate)
        except Exception as exc:
            print(f"[Diburit] edge preview failed: {exc}", file=sys.stderr)
        return

    if voice.startswith(GTTS_PREFIX):
        gtts_lang = voice[len(GTTS_PREFIX):]
        try:
            from gtts import gTTS  # type: ignore
        except ImportError:
            platform_compat.notify("Diburit", "gtts not installed. Run: pip install gtts")
            return
        tmp_mp3 = Path(_tempfile.mktemp(suffix=".mp3", dir=str(DIBURIT_HOME)))
        try:
            gTTS(text=text, lang=gtts_lang).save(str(tmp_mp3))
            if tmp_mp3.exists() and tmp_mp3.stat().st_size > 0:
                platform_compat.play_audio_nonblocking(tmp_mp3, volume, rate)
        except Exception as exc:
            print(f"[Diburit] gtts preview failed: {exc}", file=sys.stderr)
        return

    # SAPI5 via pyttsx3
    try:
        import pyttsx3  # type: ignore
    except ImportError:
        platform_compat.notify("Diburit", "pyttsx3 not installed for voice preview")
        return
    try:
        engine = pyttsx3.init()
        engine.setProperty("volume", volume)
        engine.say(text)
        engine.runAndWait()
    except Exception as exc:
        print(f"[Diburit] pyttsx3 preview failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Preferences window (tkinter)
# ---------------------------------------------------------------------------

class PrefsWindow:
    def __init__(self, app: "DiburitApp") -> None:
        self._app  = app
        self._top: Optional[tk.Toplevel] = None
        self._voice_ids: List[str] = []
        self._capturing_hotkey = False

    def show(self) -> None:
        if self._top is not None and self._top.winfo_exists():
            self._top.lift()
            self._top.focus_force()
            return
        self._top = tk.Toplevel(self._app._root)
        self._top.title("Diburit — Preferences")
        self._top.resizable(False, False)
        self._top.protocol("WM_DELETE_WINDOW", self._on_done)
        self._build()
        self._populate()

    # ── build ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        f = ttk.Frame(self._top, padding=20)
        f.grid(row=0, column=0, sticky="nsew")

        def lbl(text: str, row: int) -> None:
            ttk.Label(f, text=text, anchor="e", width=16).grid(
                row=row, column=0, sticky="e", padx=(0, 8), pady=6,
            )

        # Voice
        lbl("Voice:", 0)
        self._voice_var = tk.StringVar()
        self._voice_combo = ttk.Combobox(
            f, textvariable=self._voice_var, width=34, state="readonly",
        )
        self._voice_combo.grid(row=0, column=1, sticky="w")
        self._voice_combo.bind("<<ComboboxSelected>>", self._on_voice_changed)

        # Volume
        lbl("Volume:", 1)
        vol_f = ttk.Frame(f)
        vol_f.grid(row=1, column=1, sticky="w")
        self._vol_var = tk.DoubleVar()
        ttk.Scale(vol_f, from_=0.0, to=1.0, variable=self._vol_var, length=200,
                  command=self._on_volume_changed).pack(side="left")
        self._vol_lbl = ttk.Label(vol_f, text=" 80%", width=5)
        self._vol_lbl.pack(side="left")

        # Speed
        lbl("Speed:", 2)
        spd_f = ttk.Frame(f)
        spd_f.grid(row=2, column=1, sticky="w")
        self._spd_var = tk.DoubleVar()
        self._spd_slider = ttk.Scale(
            spd_f, from_=MIN_SPEECH_RATE, to=MAX_SPEECH_RATE,
            variable=self._spd_var, length=200,
            command=self._on_speed_changed,
        )
        self._spd_slider.pack(side="left")
        self._spd_lbl = ttk.Label(spd_f, text="1.00x", width=6)
        self._spd_lbl.pack(side="left")
        self._spd_slider.bind("<ButtonRelease-1>", self._on_speed_released)

        # Hotkey
        lbl("Hotkey:", 3)
        self._hotkey_btn = ttk.Button(f, text="", command=self._on_hotkey_click, width=28)
        self._hotkey_btn.grid(row=3, column=1, sticky="w")

        # Max recordings
        lbl("Max recordings:", 4)
        self._max_var = tk.StringVar()
        max_e = ttk.Entry(f, textvariable=self._max_var, width=8)
        max_e.grid(row=4, column=1, sticky="w")
        max_e.bind("<FocusOut>", self._on_max_changed)
        max_e.bind("<Return>",   self._on_max_changed)

        # Show status rows
        self._show_var = tk.BooleanVar()
        ttk.Checkbutton(
            f, text="Show Last & Transcript in tray menu",
            variable=self._show_var, command=self._on_show_status_changed,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=(0, 0), pady=(6, 2))

        # Buttons
        btn_f = ttk.Frame(f)
        btn_f.grid(row=6, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_f, text="Prune Recordings Now", command=self._on_prune).pack(side="left", padx=4)
        ttk.Button(btn_f, text="Done", command=self._on_done).pack(side="left", padx=4)

    # ── populate ───────────────────────────────────────────────────────────

    def _populate(self) -> None:
        a = self._app
        # Build voice list: Edge + gTTS + installed SAPI5 Hebrew voices
        all_voices: List[Tuple[str, str]] = list(EDGE_HEBREW_VOICES) + list(GTTS_HEBREW_VOICES)
        for v in platform_compat.list_tts_voices():
            all_voices.append((v["name"], v["id"]))

        self._voice_ids = [vid for _, vid in all_voices]
        self._voice_combo["values"] = [lbl for lbl, _ in all_voices]

        if a.voice in self._voice_ids:
            self._voice_combo.current(self._voice_ids.index(a.voice))
        else:
            self._voice_combo["values"] = list(self._voice_combo["values"]) + [f"{a.voice}  (not installed)"]
            self._voice_ids.append(a.voice)
            self._voice_combo.current(len(self._voice_ids) - 1)

        self._vol_var.set(a.volume)
        self._vol_lbl.config(text=f"{int(round(a.volume * 100)):3d}%")
        self._spd_var.set(a.speech_rate)
        self._spd_lbl.config(text=f"{a.speech_rate:.2f}x")
        self._hotkey_btn.config(text=_hotkey_display(a.hotkey))
        self._max_var.set(str(a.max_recordings_kept))
        self._show_var.set(a.show_status_rows)

    # ── actions ────────────────────────────────────────────────────────────

    def _on_voice_changed(self, _event=None) -> None:
        idx = self._voice_combo.current()
        if not (0 <= idx < len(self._voice_ids)):
            return
        vid = self._voice_ids[idx]
        if vid == self._app.voice:
            return
        self._app.voice = vid
        self._app._persist_settings()
        threading.Thread(
            target=_play_sample,
            args=("שלום, איך הולך?", vid, self._app.volume, self._app.speech_rate),
            daemon=True,
        ).start()

    def _on_volume_changed(self, _val=None) -> None:
        v = round(float(self._vol_var.get()), 2)
        v = max(0.0, min(v, 1.0))
        self._app.volume = v
        self._app._persist_settings()
        self._vol_lbl.config(text=f"{int(round(v * 100)):3d}%")

    def _on_speed_changed(self, _val=None) -> None:
        v = round(float(self._spd_var.get()), 2)
        v = max(MIN_SPEECH_RATE, min(v, MAX_SPEECH_RATE))
        self._app.speech_rate = v
        self._app._persist_settings()
        self._spd_lbl.config(text=f"{v:.2f}x")

    def _on_speed_released(self, _event=None) -> None:
        threading.Thread(
            target=_play_sample,
            args=("שלום, איך הולך?", self._app.voice, self._app.volume, self._app.speech_rate),
            daemon=True,
        ).start()

    def _on_hotkey_click(self) -> None:
        if self._capturing_hotkey:
            self._stop_hotkey_capture()
            return
        self._capturing_hotkey = True
        self._hotkey_btn.config(text="Press a key combo… (Esc to cancel)")
        self._app._uninstall_hotkey_listener()
        self._top.bind("<KeyPress>", self._on_hotkey_keypress)
        self._top.focus_force()

    def _on_hotkey_keypress(self, event: tk.Event) -> None:
        if event.keysym == "Escape":
            self._stop_hotkey_capture()
            return
        parts: List[str] = []
        mods = event.state
        # tkinter modifier bitmasks
        if mods & 0x0004:
            parts.append("<ctrl>")
        if mods & 0x0001:
            parts.append("<shift>")
        if mods & 0x20000:
            parts.append("<alt>")
        key = event.keysym.lower()
        # Map special keys
        if key.startswith("f") and key[1:].isdigit():
            parts.append(f"<{key}>")
        elif len(key) == 1:
            parts.append(key)
        else:
            return  # unrecognised key, keep listening
        if not parts or (len(parts) == 1 and not parts[0].startswith("<")):
            return  # no modifiers + plain letter = unlikely to be a hotkey intent

        # Always store in macOS form: <ctrl> stays, but remap to <cmd> for
        # cross-platform compat if the user hit only Ctrl (no Alt/Shift edge cases).
        chord = "+".join(parts)
        # Normalise: on Windows we store <cmd> for macOS compat. Map <ctrl>→<cmd>:
        chord_stored = chord.replace("<ctrl>", "<cmd>")
        self._stop_hotkey_capture()
        self._app._apply_hotkey(chord_stored)
        self._hotkey_btn.config(text=_hotkey_display(self._app.hotkey))

    def _stop_hotkey_capture(self) -> None:
        self._capturing_hotkey = False
        if self._top is not None and self._top.winfo_exists():
            self._top.unbind("<KeyPress>")
        self._hotkey_btn.config(text=_hotkey_display(self._app.hotkey))
        self._app._install_hotkey_listener()

    def _on_max_changed(self, _event=None) -> None:
        try:
            n = int(self._max_var.get())
        except ValueError:
            self._max_var.set(str(self._app.max_recordings_kept))
            return
        self._app._apply_max_recordings(n)
        self._max_var.set(str(self._app.max_recordings_kept))

    def _on_show_status_changed(self) -> None:
        self._app.show_status_rows = self._show_var.get()
        self._app._persist_settings()

    def _on_prune(self) -> None:
        self._app.on_prune_now()

    def _on_done(self) -> None:
        if self._capturing_hotkey:
            self._stop_hotkey_capture()
        if self._top is not None and self._top.winfo_exists():
            self._top.destroy()
        self._top = None


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

_ACT_TOGGLE_RECORDING = "toggle_recording"
_ACT_START_RECORDING  = "start_recording"
_ACT_STOP_RECORDING   = "stop_recording"
_ACT_CANCEL_RECORDING = "cancel_recording"
_ACT_REFRESH          = "refresh"
_ACT_OPEN_PREFS       = "open_prefs"
_ACT_OPEN_FOLDER      = "open_folder"
_ACT_QUIT             = "quit"
_ACT_TOGGLE_ENABLED   = "toggle_enabled"
_ACT_TOGGLE_PTT       = "toggle_ptt"


class DiburitApp:
    def __init__(self) -> None:
        settings = _load_settings(defaults=DEFAULT_SETTINGS)
        self.voice:              str   = str(settings.get("voice",               DEFAULT_SETTINGS["voice"]))
        self.volume:             float = float(settings.get("volume",            DEFAULT_SETTINGS["volume"]))  # type: ignore
        self.hotkey:             str   = str(settings.get("hotkey",              DEFAULT_SETTINGS["hotkey"]))
        self.hotkey_mode:        str   = str(settings.get("hotkey_mode",         DEFAULT_SETTINGS["hotkey_mode"]))
        self.max_recordings_kept: int  = int(settings.get("max_recordings_kept", 100))  # type: ignore
        self.speech_rate:        float = float(settings.get("speech_rate",       DEFAULT_SETTINGS["speech_rate"]))  # type: ignore
        self.show_status_rows:   bool  = bool(settings.get("show_status_rows",   DEFAULT_SETTINGS["show_status_rows"]))

        self.enabled:      bool = True
        self.recording:    bool = False
        self.transcribing: bool = False

        self._buffer:           List[np.ndarray]    = []
        self._stream:           Optional[sd.InputStream] = None
        self._record_started_at: float = 0.0
        self._current_utterance: Optional[Utterance] = None
        self.last_recording_path: Optional[str] = None
        self.last_transcript:     Optional[str] = None

        self._main_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()

        # pystray icon (set in run())
        self._icon: Optional[object] = None
        # tkinter hidden root (set in run())
        self._root: Optional[tk.Tk] = None

        self._hotkey_listener: Optional[object] = None
        self._prefs_window:    Optional[PrefsWindow] = None

    # ── entry point ────────────────────────────────────────────────────────

    def run(self) -> None:
        import pystray  # type: ignore

        DIBURIT_HOME.mkdir(parents=True, exist_ok=True)
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        _redirect_to_log()

        print(f"[Diburit] === starting v{__version__} pid={os.getpid()} ===", flush=True)

        # 1. Hidden Tk root — must exist before any Toplevel
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title("Diburit")

        # 2. pystray in a background thread
        self._icon = pystray.Icon(
            "Diburit",
            _make_icon("idle"),
            "Diburit",
            self._build_menu(),
        )
        self._icon.run_detached()  # type: ignore

        # 3. Hotkey
        self._install_hotkey_listener()

        # 4. Pump on tkinter's main loop
        self._root.after(PUMP_INTERVAL_MS, self._pump)

        # 5. tkinter owns the main thread
        self._root.mainloop()

    # ── pystray menu (dynamic) ─────────────────────────────────────────────

    def _build_menu(self):
        import pystray  # type: ignore

        app = self

        def _menu_items():
            status = "ON" if app.enabled else "OFF"
            record_label = (
                "Stop Recording"  if app.recording else
                "Hold to Record"  if app.hotkey_mode == HOTKEY_MODE_PTT else
                "Start Recording"
            )
            hk_display = _hotkey_display(app.hotkey)
            items = [
                pystray.MenuItem(
                    f"Status: {status}",
                    lambda icon, item: app._main_queue.put((_ACT_TOGGLE_ENABLED, None)),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    f"{record_label}  ({hk_display})",
                    lambda icon, item: app._main_queue.put((_ACT_TOGGLE_RECORDING, None)),
                ),
                pystray.MenuItem(
                    "Push-to-Talk Mode",
                    lambda icon, item: app._main_queue.put((_ACT_TOGGLE_PTT, None)),
                    checked=lambda item: app.hotkey_mode == HOTKEY_MODE_PTT,
                ),
            ]
            if app.show_status_rows:
                last_label = (
                    f"Last: {os.path.basename(os.path.dirname(app.last_recording_path))}"
                    if app.last_recording_path else "Last recording: (none)"
                )
                tr = app.last_transcript or ""
                if app.transcribing:
                    tr_label = "Transcript: transcribing…"
                elif tr:
                    tr_label = f"Transcript: {tr[:TRANSCRIPT_PREVIEW_CHARS]}{'…' if len(tr) > TRANSCRIPT_PREVIEW_CHARS else ''}"
                else:
                    tr_label = "Transcript: (none)"
                items += [
                    pystray.MenuItem(last_label, None, enabled=False),
                    pystray.MenuItem(tr_label,   None, enabled=False),
                ]
            items += [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Preferences…",
                    lambda icon, item: app._main_queue.put((_ACT_OPEN_PREFS, None)),
                ),
                pystray.MenuItem(
                    "Open Diburit Folder…",
                    lambda icon, item: app._main_queue.put((_ACT_OPEN_FOLDER, None)),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Quit Diburit",
                    lambda icon, item: app._main_queue.put((_ACT_QUIT, None)),
                ),
            ]
            return items

        return pystray.Menu(_menu_items)

    def _refresh_icon(self) -> None:
        if self._icon is None:
            return
        if self.recording:
            state = "recording"
        elif self.transcribing:
            state = "transcribing"
        elif not self.enabled:
            state = "disabled"
        elif self.hotkey_mode == HOTKEY_MODE_PTT:
            state = "idle_ptt"
        else:
            state = "idle"
        try:
            self._icon.icon = _make_icon(state)  # type: ignore
        except Exception:
            pass

    # ── pump ───────────────────────────────────────────────────────────────

    def _pump(self) -> None:
        while True:
            try:
                action, payload = self._main_queue.get_nowait()
            except queue.Empty:
                break
            self._handle(action, payload)
        if self._root is not None:
            self._root.after(PUMP_INTERVAL_MS, self._pump)

    def _handle(self, action: str, payload: object) -> None:
        if action == _ACT_TOGGLE_RECORDING:
            self.on_toggle_recording()
        elif action == _ACT_START_RECORDING:
            if self.enabled and not self.recording:
                self._start_recording()
        elif action == _ACT_STOP_RECORDING:
            if self.recording:
                self._stop_recording()
        elif action == _ACT_CANCEL_RECORDING:
            # PTT tap shorter than PTT_MIN_HOLD_SEC — discard buffer silently
            if self.recording:
                self.recording = False
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
                self._buffer = []
                self._current_utterance = None
                self._refresh_icon()
        elif action == _ACT_REFRESH:
            if isinstance(payload, dict):
                for k, v in payload.items():
                    setattr(self, k, v)
            self._refresh_icon()
        elif action == _ACT_TOGGLE_ENABLED:
            self.on_toggle_enabled()
        elif action == _ACT_TOGGLE_PTT:
            self.on_toggle_ptt_mode()
        elif action == _ACT_OPEN_PREFS:
            self.on_open_preferences()
        elif action == _ACT_OPEN_FOLDER:
            platform_compat.open_folder(DIBURIT_HOME)
        elif action == _ACT_QUIT:
            self.on_quit()

    def _enqueue_refresh(self, **state_updates) -> None:
        self._main_queue.put((_ACT_REFRESH, state_updates))

    # ── settings ───────────────────────────────────────────────────────────

    def _persist_settings(self) -> None:
        _save_settings({
            "voice":               self.voice,
            "volume":              self.volume,
            "hotkey":              self.hotkey,
            "hotkey_mode":         self.hotkey_mode,
            "max_recordings_kept": self.max_recordings_kept,
            "speech_rate":         self.speech_rate,
            "show_status_rows":    self.show_status_rows,
        })

    def _apply_max_recordings(self, n: int) -> None:
        n = max(10, min(int(n), 10_000))
        if n == self.max_recordings_kept:
            return
        self.max_recordings_kept = n
        self._persist_settings()

    # ── hotkey ─────────────────────────────────────────────────────────────

    def _install_hotkey_listener(self) -> None:
        self._uninstall_hotkey_listener()
        if not self.hotkey:
            return
        if self.hotkey_mode == HOTKEY_MODE_PTT:
            self._install_ptt_listener()
        else:
            self._install_toggle_listener()

    def _install_toggle_listener(self) -> None:
        required = _parse_pynput_chord(self.hotkey)
        held: Set = set()
        fired = {"on": False}  # prevent re-firing while chord is held down

        def on_press(key: object) -> None:
            canonical = _norm(key)
            held.add(canonical)
            if not fired["on"] and required.issubset(held):
                fired["on"] = True
                self._main_queue.put((_ACT_TOGGLE_RECORDING, None))

        def on_release(key: object) -> None:
            canonical = _norm(key)
            held.discard(canonical)
            if fired["on"] and canonical in required:
                fired["on"] = False

        listener_ref: List[object] = [None]
        event_filter = _make_event_filter(listener_ref, self.hotkey)
        try:
            listener = kb.Listener(
                on_press=on_press, on_release=on_release,
                win32_event_filter=event_filter,
            )
            listener_ref[0] = listener
            listener.start()
            self._hotkey_listener = listener
            pynput_str = _hotkey_to_pynput_str(self.hotkey)
            print(f"[Diburit] hotkey (toggle): {self.hotkey} → {pynput_str}", flush=True)
        except Exception as exc:
            print(f"[Diburit] hotkey install failed: {exc}", file=sys.stderr)
            self._hotkey_listener = None

    def _install_ptt_listener(self) -> None:
        required  = _parse_pynput_chord(self.hotkey)
        held: Set = set()
        active    = {"on": False, "t0": 0.0}

        def on_press(key: object) -> None:
            canonical = _norm(key)
            held.add(canonical)
            if not active["on"] and required.issubset(held):
                active["on"] = True
                active["t0"] = time.monotonic()
                self._main_queue.put((_ACT_START_RECORDING, None))

        def on_release(key: object) -> None:
            canonical = _norm(key)
            held.discard(canonical)
            if active["on"] and canonical in required:
                active["on"] = False
                held_for = time.monotonic() - active["t0"]
                if held_for >= PTT_MIN_HOLD_SEC:
                    self._main_queue.put((_ACT_STOP_RECORDING, None))
                else:
                    self._main_queue.put((_ACT_CANCEL_RECORDING, None))

        listener_ref: List[object] = [None]
        event_filter = _make_event_filter(listener_ref, self.hotkey)
        try:
            listener = kb.Listener(
                on_press=on_press, on_release=on_release,
                win32_event_filter=event_filter,
            )
            listener_ref[0] = listener
            listener.start()
            self._hotkey_listener = listener
            print(f"[Diburit] hotkey (PTT): {self.hotkey}", flush=True)
        except Exception as exc:
            print(f"[Diburit] PTT hotkey install failed: {exc}", file=sys.stderr)
            self._hotkey_listener = None

    def _uninstall_hotkey_listener(self) -> None:
        listener = self._hotkey_listener
        self._hotkey_listener = None
        if listener is None:
            return
        try:
            listener.stop()  # type: ignore
        except Exception as exc:
            print(f"[Diburit] error stopping hotkey: {exc}", file=sys.stderr)

    def _apply_hotkey(self, candidate: str) -> None:
        candidate = (candidate or "").strip()
        if not candidate or candidate == self.hotkey:
            return
        old = self.hotkey
        self._uninstall_hotkey_listener()
        self.hotkey = candidate
        self._install_hotkey_listener()
        if self._hotkey_listener is None:
            platform_compat.notify("Diburit", f"Could not register {_hotkey_display(candidate)}")
            self.hotkey = old
            self._install_hotkey_listener()
            return
        self._persist_settings()
        platform_compat.notify("Diburit", f"Hotkey: {_hotkey_display(candidate)}")

    # ── menu callbacks ─────────────────────────────────────────────────────

    def on_toggle_enabled(self) -> None:
        if self.recording:
            self._stop_recording()
        self.enabled = not self.enabled
        self._refresh_icon()
        platform_compat.notify("Diburit", "Enabled" if self.enabled else "Disabled")

    def on_toggle_recording(self) -> None:
        if not self.enabled:
            platform_compat.notify("Diburit", "Disabled — toggle Status to ON first")
            return
        if self.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def on_toggle_ptt_mode(self) -> None:
        if self.recording:
            self._stop_recording()
        new_mode = HOTKEY_MODE_TOGGLE if self.hotkey_mode == HOTKEY_MODE_PTT else HOTKEY_MODE_PTT
        self._uninstall_hotkey_listener()
        self.hotkey_mode = new_mode
        self._install_hotkey_listener()
        if self._hotkey_listener is None:
            self.hotkey_mode = HOTKEY_MODE_TOGGLE if new_mode == HOTKEY_MODE_PTT else HOTKEY_MODE_PTT
            self._install_hotkey_listener()
            platform_compat.notify("Diburit", "Could not switch hotkey mode")
            return
        self._persist_settings()
        self._refresh_icon()
        platform_compat.notify(
            "Diburit",
            "Push-to-Talk: hold the hotkey to record"
            if self.hotkey_mode == HOTKEY_MODE_PTT
            else "Toggle: press to start, press again to stop",
        )

    def on_open_preferences(self) -> None:
        if self._prefs_window is None:
            self._prefs_window = PrefsWindow(self)
        self._prefs_window.show()

    def on_prune_now(self) -> None:
        def _do() -> None:
            _prune_recordings(self.max_recordings_kept)
            platform_compat.notify("Diburit", f"Pruned recordings (keeping {self.max_recordings_kept})")
        threading.Thread(target=_do, daemon=True).start()

    def on_quit(self) -> None:
        if self.recording:
            self._stop_recording()
        if self.transcribing:
            platform_compat.notify("Diburit", "Finishing transcription…")
            deadline = time.monotonic() + QUIT_TRANSCRIBE_GRACE_SEC
            while self.transcribing and time.monotonic() < deadline:
                time.sleep(0.1)
        self._uninstall_hotkey_listener()
        if self._icon is not None:
            try:
                self._icon.stop()  # type: ignore
            except Exception:
                pass
        if self._root is not None:
            self._root.quit()

    # ── recording ──────────────────────────────────────────────────────────

    def _start_recording(self) -> None:
        if self.transcribing:
            platform_compat.notify("Diburit", "Still transcribing previous recording")
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
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                callback=_callback,
            )
            stream.start()
        except sd.PortAudioError as exc:
            msg = str(exc)
            if "Invalid device" in msg or "Unanticipated" in msg or "Access" in msg:
                platform_compat.notify(
                    "Diburit",
                    "Mic access denied — check Settings → Privacy & Security → Microphone",
                )
            else:
                platform_compat.notify("Diburit", f"Could not start mic: {msg[:100]}")
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            return
        except Exception as exc:
            platform_compat.notify("Diburit", f"Could not start: {str(exc)[:100]}")
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            return

        self._stream = stream
        self.recording = True
        self._record_started_at = time.monotonic()
        self._refresh_icon()
        print("[Diburit] recording started", flush=True)

    def _stop_recording(self) -> None:
        if not self.recording:
            return
        self.recording = False
        duration = time.monotonic() - self._record_started_at

        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception as exc:
                print(f"[Diburit] stream stop error: {exc}", file=sys.stderr)
            try:
                self._stream.close()
            except Exception as exc:
                print(f"[Diburit] stream close error: {exc}", file=sys.stderr)
            self._stream = None

        utterance = self._current_utterance
        self._current_utterance = None
        self._refresh_icon()

        if not self._buffer or utterance is None:
            print("[Diburit] stopped with empty buffer", flush=True)
            platform_compat.notify("Diburit", "Stopped (no audio captured)")
            return

        data = np.concatenate(self._buffer, axis=0)
        self._buffer = []

        if _audio_is_silent(data):
            print(f"[Diburit] silent recording ({duration:.1f}s)", flush=True)
            platform_compat.notify(
                "Diburit",
                "⚠ Mic is silent — check Settings → Privacy & Security → Microphone",
            )
            return

        sf.write(str(utterance.audio_path), data, SAMPLE_RATE, subtype="PCM_16")
        self.last_recording_path = str(utterance.audio_path)
        print(f"[Diburit] recorded {duration:.1f}s → {utterance.audio_path}", flush=True)
        self._start_transcription(utterance)

    # ── transcription ──────────────────────────────────────────────────────

    def _start_transcription(self, utterance: Utterance) -> None:
        self.transcribing = True
        self._refresh_icon()
        threading.Thread(
            target=self._transcribe_worker, args=(utterance,), daemon=True,
        ).start()

    def _transcribe_worker(self, utterance: Utterance) -> None:
        try:
            text = _transcribe_with_groq(utterance.audio_path)
        except Exception as exc:
            print(f"[Diburit] transcription failed: {exc}", file=sys.stderr)
            platform_compat.notify("Diburit", f"Transcription failed: {str(exc)[:120]}")
            self._enqueue_refresh(transcribing=False)
            return

        if _is_silence_hallucination(text):
            print(f"[Diburit] dropped hallucination {text!r}", flush=True)
            platform_compat.notify("Diburit", "⚠ Silent recording (Whisper hallucination filtered)")
            self._enqueue_refresh(transcribing=False)
            return

        utterance.transcript = text
        try:
            _atomic_write(utterance.transcript_path, text + "\n")
        except OSError as exc:
            print(f"[Diburit] could not write transcript: {exc}", file=sys.stderr)

        platform_compat.copy_to_clipboard(text)
        pasted, target_app = self._paste_into_frontmost()
        utterance.target_app = target_app
        if pasted:
            utterance.pasted_at = time.time()

        try:
            utterance.write_metadata()
            _repoint_latest(utterance.dir)
        except OSError as exc:
            print(f"[Diburit] could not finalize metadata: {exc}", file=sys.stderr)

        print(f"[Diburit] transcript: {text}", flush=True)
        if not pasted:
            preview = text if len(text) <= NOTIFICATION_PREVIEW_CHARS else text[:NOTIFICATION_PREVIEW_CHARS - 3] + "…"
            if target_app:
                prefix = f"⚠ paste skipped ({target_app}, on clipboard): "
            else:
                prefix = "⚠ paste failed (clipboard only): "
            platform_compat.notify("Diburit", prefix + preview)

        self._enqueue_refresh(transcribing=False, last_transcript=text)
        _prune_recordings(self.max_recordings_kept)

    def _paste_into_frontmost(self) -> Tuple[bool, str]:
        front = platform_compat.get_frontmost_app()
        if not front or front in _PASTE_BLOCKLIST:
            print(f"[Diburit] paste skipped (focus: {front!r})", file=sys.stderr)
            return False, front or ""
        try:
            platform_compat.send_paste()
        except Exception as exc:
            print(f"[Diburit] paste failed: {exc}", file=sys.stderr)
            return False, front
        print(f"[Diburit] pasted into {front!r}", flush=True)
        return True, front


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _redirect_to_log() -> None:
    """Redirect stdout/stderr to ~/Diburit/runtime.log."""
    log_path = DIBURIT_HOME / "runtime.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "a", encoding="utf-8")
    sys.stdout = fh
    sys.stderr = fh


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    DiburitApp().run()


if __name__ == "__main__":
    main()
