"""Windows-only tests for pynput hotkey chord logic.

These test _norm, _parse_pynput_chord, _hotkey_display, and the full
chord-detection simulation. All tests are skipped on non-Windows platforms.

pynput itself is cross-platform, but the functions under test are specific
to diburit_win.py and its Ctrl-mapping of <cmd>.

Run (Windows):
    $env:PYTHONIOENCODING="utf-8"
    .venv\Scripts\python -m pytest tests/test_hotkey_win.py -v
"""
from __future__ import annotations

import sys
import unittest

from pynput import keyboard as kb

_IS_WINDOWS = sys.platform == "win32"
_SKIP_MSG   = "Windows-only (diburit_win.py hotkey logic)"

if _IS_WINDOWS:
    sys.path.insert(0, __import__("pathlib").Path(__file__).parent.parent.__str__())
    from diburit_win import (
        _KEY_NORMALISE,
        _hotkey_display,
        _norm,
        _parse_pynput_chord,
    )


# ---------------------------------------------------------------------------
# 1. _norm()
# ---------------------------------------------------------------------------

@unittest.skipUnless(_IS_WINDOWS, _SKIP_MSG)
class TestNorm(unittest.TestCase):

    def test_ctrl_l_to_ctrl(self):
        self.assertEqual(_norm(kb.Key.ctrl_l), kb.Key.ctrl)

    def test_ctrl_r_to_ctrl(self):
        self.assertEqual(_norm(kb.Key.ctrl_r), kb.Key.ctrl)

    def test_shift_l_to_shift(self):
        self.assertEqual(_norm(kb.Key.shift_l), kb.Key.shift)

    def test_shift_r_to_shift(self):
        self.assertEqual(_norm(kb.Key.shift_r), kb.Key.shift)

    def test_alt_l_to_alt(self):
        self.assertEqual(_norm(kb.Key.alt_l), kb.Key.alt)

    def test_alt_r_to_alt(self):
        self.assertEqual(_norm(kb.Key.alt_r), kb.Key.alt)

    def test_alt_gr_to_alt(self):
        self.assertEqual(_norm(kb.Key.alt_gr), kb.Key.alt)

    def test_generic_ctrl_unchanged(self):
        self.assertEqual(_norm(kb.Key.ctrl), kb.Key.ctrl)

    def test_generic_shift_unchanged(self):
        self.assertEqual(_norm(kb.Key.shift), kb.Key.shift)

    def test_keycode_uppercase_lowercased(self):
        # Shift+M reports KeyCode(char='M', vk=77) — must become KeyCode('m')
        result = _norm(kb.KeyCode(char='M', vk=77))
        self.assertEqual(result, kb.KeyCode.from_char('m'))

    def test_keycode_lowercase_unchanged(self):
        result = _norm(kb.KeyCode(char='m', vk=77))
        self.assertEqual(result, kb.KeyCode.from_char('m'))

    def test_keycode_from_char_passthrough(self):
        result = _norm(kb.KeyCode.from_char('m'))
        self.assertEqual(result, kb.KeyCode.from_char('m'))

    def test_keycode_no_char_passthrough(self):
        k = kb.KeyCode(vk=200)
        self.assertIs(_norm(k), k)

    def test_unrecognised_key_passthrough(self):
        self.assertEqual(_norm(kb.Key.f5), kb.Key.f5)


# ---------------------------------------------------------------------------
# 2. _parse_pynput_chord()
# ---------------------------------------------------------------------------

@unittest.skipUnless(_IS_WINDOWS, _SKIP_MSG)
class TestParseChord(unittest.TestCase):

    def test_cmd_shift_m(self):
        r = _parse_pynput_chord("<cmd>+<shift>+m")
        self.assertEqual(len(r), 3)
        self.assertIn(kb.Key.ctrl,  r)
        self.assertIn(kb.Key.shift, r)
        self.assertIn(kb.KeyCode.from_char('m'), r)

    def test_ctrl_shift_m_same_as_cmd_shift_m(self):
        self.assertEqual(
            _parse_pynput_chord("<cmd>+<shift>+m"),
            _parse_pynput_chord("<ctrl>+<shift>+m"),
        )

    def test_f12_single_key(self):
        r = _parse_pynput_chord("f12")
        self.assertEqual(len(r), 1)
        self.assertIn(kb.Key.f12, r)

    def test_alt_h(self):
        r = _parse_pynput_chord("<alt>+h")
        self.assertIn(kb.Key.alt, r)
        self.assertIn(kb.KeyCode.from_char('h'), r)

    def test_empty_spec_returns_empty(self):
        self.assertEqual(len(_parse_pynput_chord("")), 0)

    def test_unknown_token_ignored(self):
        r = _parse_pynput_chord("<nonsense>+m")
        self.assertIn(kb.KeyCode.from_char('m'), r)
        self.assertNotIn(kb.Key.ctrl, r)


# ---------------------------------------------------------------------------
# 3. Chord detection simulation
# ---------------------------------------------------------------------------

@unittest.skipUnless(_IS_WINDOWS, _SKIP_MSG)
class TestChordDetection(unittest.TestCase):

    def setUp(self):
        self.required = _parse_pynput_chord("<cmd>+<shift>+m")

    def _press(self, held: set, key) -> bool:
        held.add(_norm(key))
        return self.required.issubset(held)

    def _release(self, held: set, key) -> None:
        held.discard(_norm(key))

    def test_ctrl_l_shift_l_uppercase_M_triggers(self):
        held: set = set()
        self._press(held, kb.Key.ctrl_l)
        self._press(held, kb.Key.shift_l)
        self.assertTrue(self._press(held, kb.KeyCode(char='M', vk=77)))

    def test_ctrl_r_shift_r_lowercase_m_triggers(self):
        held: set = set()
        self._press(held, kb.Key.ctrl_r)
        self._press(held, kb.Key.shift_r)
        self.assertTrue(self._press(held, kb.KeyCode(char='m', vk=77)))

    def test_only_ctrl_does_not_trigger(self):
        held: set = set()
        self._press(held, kb.Key.ctrl_l)
        self.assertFalse(self._press(held, kb.KeyCode(char='m', vk=77)))

    def test_only_modifiers_no_char_does_not_trigger(self):
        held: set = set()
        self._press(held, kb.Key.ctrl_l)
        self._press(held, kb.Key.shift_l)
        self.assertFalse(self.required.issubset(held))

    def test_after_release_chord_cleared(self):
        held: set = set()
        self._press(held, kb.Key.ctrl_l)
        self._press(held, kb.Key.shift_l)
        self._press(held, kb.KeyCode(char='M', vk=77))
        self._release(held, kb.KeyCode(char='M', vk=77))
        self.assertFalse(self.required.issubset(held))

    def test_f12_single_key_chord(self):
        required_f12 = _parse_pynput_chord("f12")
        held: set = set()
        held.add(_norm(kb.Key.f12))
        self.assertTrue(required_f12.issubset(held))

    def test_wrong_char_does_not_trigger(self):
        held: set = set()
        self._press(held, kb.Key.ctrl_l)
        self._press(held, kb.Key.shift_l)
        self._press(held, kb.KeyCode(char='x', vk=88))
        self.assertFalse(self.required.issubset(held))


# ---------------------------------------------------------------------------
# 4. _hotkey_display()
# ---------------------------------------------------------------------------

@unittest.skipUnless(_IS_WINDOWS, _SKIP_MSG)
class TestHotkeyDisplay(unittest.TestCase):

    def test_cmd_shift_m(self):
        self.assertEqual(_hotkey_display("<cmd>+<shift>+m"), "Ctrl+Shift+m")

    def test_ctrl_shift_m(self):
        self.assertEqual(_hotkey_display("<ctrl>+<shift>+m"), "Ctrl+Shift+m")

    def test_alt_h(self):
        self.assertEqual(_hotkey_display("<alt>+h"), "Alt+h")

    def test_f12(self):
        self.assertEqual(_hotkey_display("f12"), "f12")

    def test_option_mapped_to_alt(self):
        self.assertEqual(_hotkey_display("<option>+m"), "Alt+m")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
