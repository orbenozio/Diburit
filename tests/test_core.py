"""Cross-platform tests for diburit_core.

Tests all shared logic: settings, atomic write, silence detection,
hallucination filter. Imports ONLY from diburit_core — runs on both
macOS and Windows without any platform-specific dependencies.

Run:
    $env:PYTHONIOENCODING="utf-8"
    .venv/Scripts/python -m pytest tests/test_core.py -v      # Windows
    .venv/bin/python -m pytest tests/test_core.py -v          # macOS
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
import diburit_core as core
from diburit_core import (
    HOTKEY_MODE_PTT,
    HOTKEY_MODE_TOGGLE,
    MAX_SPEECH_RATE,
    MAX_TYPE_CPS,
    MIN_SPEECH_RATE,
    MIN_TYPE_CPS,
    PASTE_MODE_PASTE,
    PASTE_MODE_TYPE,
    SILENCE_PEAK_THRESHOLD,
    _BASE_SETTINGS,
    _atomic_write,
    _audio_is_silent,
    _is_silence_hallucination,
    _load_settings,
    _prune_recordings,
    edge_rate_str,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)


def _write_bom(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode("utf-8"))


def _defaults(**overrides) -> dict:
    return {**_BASE_SETTINGS, "voice": "TestVoice", **overrides}


# ---------------------------------------------------------------------------
# 1. _atomic_write
# ---------------------------------------------------------------------------

class TestAtomicWrite(unittest.TestCase):

    def test_writes_content(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "out.json"
            _atomic_write(p, '{"ok": true}')
            self.assertEqual(p.read_text(encoding="utf-8"), '{"ok": true}')

    def test_no_tmp_file_left(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "out.json"
            _atomic_write(p, "hello")
            self.assertFalse(p.with_suffix(".json.tmp").exists())

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "dir" / "out.json"
            _atomic_write(p, "data")
            self.assertTrue(p.exists())

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "out.json"
            _atomic_write(p, "first")
            _atomic_write(p, "second")
            self.assertEqual(p.read_text(encoding="utf-8"), "second")

    def test_hebrew_content_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "out.txt"
            _atomic_write(p, "שלום עולם")
            self.assertEqual(p.read_text(encoding="utf-8"), "שלום עולם")


# ---------------------------------------------------------------------------
# 2. _load_settings
# ---------------------------------------------------------------------------

class TestLoadSettings(unittest.TestCase):

    def _load(self, path: Path, defaults: dict = None) -> dict:
        return _load_settings(settings_file=path, defaults=defaults or _defaults())

    def test_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._load(Path(td) / "nonexistent.json")
            self.assertEqual(result["voice"], "TestVoice")
            self.assertEqual(result["hotkey"], _BASE_SETTINGS["hotkey"])

    def test_valid_settings_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({
                "voice": "edge:he-IL-AvriNeural",
                "volume": 0.5,
                "hotkey": "<ctrl>+<shift>+h",
                "hotkey_mode": "ptt",
                "max_recordings_kept": 50,
                "speech_rate": 1.5,
                "show_status_rows": False,
            }))
            result = self._load(p)
            self.assertEqual(result["voice"], "edge:he-IL-AvriNeural")
            self.assertAlmostEqual(result["volume"], 0.5)
            self.assertEqual(result["hotkey"], "<ctrl>+<shift>+h")
            self.assertEqual(result["hotkey_mode"], HOTKEY_MODE_PTT)
            self.assertEqual(result["max_recordings_kept"], 50)
            self.assertAlmostEqual(result["speech_rate"], 1.5)
            self.assertFalse(result["show_status_rows"])

    def test_bom_file_parsed_correctly(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write_bom(p, {"volume": 0.3})
            result = self._load(p)
            self.assertAlmostEqual(result["volume"], 0.3)

    def test_invalid_json_returns_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, "not json at all {{{")
            result = self._load(p)
            self.assertAlmostEqual(result["volume"], _BASE_SETTINGS["volume"])

    def test_volume_clamped_below_zero(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"volume": -5.0}))
            self.assertEqual(self._load(p)["volume"], 0.0)

    def test_volume_clamped_above_one(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"volume": 99.0}))
            self.assertEqual(self._load(p)["volume"], 1.0)

    def test_speech_rate_clamped_high(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"speech_rate": 99.9}))
            self.assertEqual(self._load(p)["speech_rate"], MAX_SPEECH_RATE)

    def test_speech_rate_clamped_low(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"speech_rate": 0.0}))
            self.assertEqual(self._load(p)["speech_rate"], MIN_SPEECH_RATE)

    def test_paste_mode_valid_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"paste_mode": PASTE_MODE_TYPE}))
            self.assertEqual(self._load(p)["paste_mode"], PASTE_MODE_TYPE)

    def test_paste_mode_invalid_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"paste_mode": "bogus"}))
            self.assertEqual(self._load(p)["paste_mode"], PASTE_MODE_PASTE)

    def test_type_cps_clamped_high(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"type_cps": 9999}))
            self.assertEqual(self._load(p)["type_cps"], MAX_TYPE_CPS)

    def test_type_cps_clamped_low(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"type_cps": 0}))
            self.assertEqual(self._load(p)["type_cps"], MIN_TYPE_CPS)

    def test_max_recordings_clamped_low(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"max_recordings_kept": 1}))
            self.assertEqual(self._load(p)["max_recordings_kept"], 10)

    def test_max_recordings_clamped_high(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"max_recordings_kept": 99999}))
            self.assertEqual(self._load(p)["max_recordings_kept"], 10_000)

    def test_unknown_hotkey_mode_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"hotkey_mode": "INVALID"}))
            result = self._load(p)
            self.assertEqual(result["hotkey_mode"], _BASE_SETTINGS["hotkey_mode"])

    def test_empty_voice_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"voice": "   "}))
            result = self._load(p)
            self.assertEqual(result["voice"], "TestVoice")

    def test_valid_backend_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"transcription_backend": "groq"}))
            self.assertEqual(self._load(p)["transcription_backend"], "groq")

    def test_unknown_backend_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"transcription_backend": "bogus"}))
            self.assertEqual(
                self._load(p)["transcription_backend"],
                _BASE_SETTINGS["transcription_backend"],
            )

    def test_valid_local_model_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"local_model": "ivrit-large"}))
            self.assertEqual(self._load(p)["local_model"], "ivrit-large")

    def test_unknown_local_model_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps({"local_model": "not-a-model"}))
            self.assertEqual(
                self._load(p)["local_model"],
                _BASE_SETTINGS["local_model"],
            )

    def test_platform_default_voice_respected(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nonexistent.json"
            mac_result = _load_settings(missing, defaults={**_BASE_SETTINGS, "voice": "Carmit"})
            win_result = _load_settings(missing, defaults={**_BASE_SETTINGS, "voice": "edge:he-IL-HilaNeural"})
            self.assertEqual(mac_result["voice"], "Carmit")
            self.assertEqual(win_result["voice"], "edge:he-IL-HilaNeural")

    def test_non_dict_json_returns_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            _write(p, json.dumps([1, 2, 3]))
            result = self._load(p)
            self.assertAlmostEqual(result["volume"], _BASE_SETTINGS["volume"])


# ---------------------------------------------------------------------------
# 2b. edge_rate_str
# ---------------------------------------------------------------------------

class TestEdgeRateStr(unittest.TestCase):

    def test_normal_is_zero_percent(self):
        self.assertEqual(edge_rate_str(1.0), "+0%")

    def test_faster_is_positive(self):
        self.assertEqual(edge_rate_str(1.5), "+50%")

    def test_slower_is_negative(self):
        self.assertEqual(edge_rate_str(0.8), "-20%")

    def test_clamped_to_speech_rate_bounds(self):
        self.assertEqual(edge_rate_str(99.0), f"{round((MAX_SPEECH_RATE - 1.0) * 100):+d}%")
        self.assertEqual(edge_rate_str(0.0), f"{round((MIN_SPEECH_RATE - 1.0) * 100):+d}%")

    def test_always_has_explicit_sign(self):
        for r in (0.5, 1.0, 1.2, 2.5):
            self.assertIn(edge_rate_str(r)[0], "+-")


# ---------------------------------------------------------------------------
# 3. _audio_is_silent
# ---------------------------------------------------------------------------

class TestAudioIsSilent(unittest.TestCase):

    def test_empty_array_is_silent(self):
        self.assertTrue(_audio_is_silent(np.array([], dtype=np.int16)))

    def test_all_zeros_is_silent(self):
        self.assertTrue(_audio_is_silent(np.zeros(1000, dtype=np.int16)))

    def test_below_threshold_is_silent(self):
        data = np.full(100, SILENCE_PEAK_THRESHOLD - 1, dtype=np.int16)
        self.assertTrue(_audio_is_silent(data))

    def test_at_threshold_is_not_silent(self):
        # condition is strictly < threshold
        data = np.full(100, SILENCE_PEAK_THRESHOLD, dtype=np.int16)
        self.assertFalse(_audio_is_silent(data))

    def test_above_threshold_not_silent(self):
        data = np.full(100, SILENCE_PEAK_THRESHOLD + 1, dtype=np.int16)
        self.assertFalse(_audio_is_silent(data))

    def test_negative_peak_detected(self):
        data = np.full(100, -(SILENCE_PEAK_THRESHOLD + 1), dtype=np.int16)
        self.assertFalse(_audio_is_silent(data))

    def test_single_loud_sample_in_silence(self):
        data = np.zeros(1000, dtype=np.int16)
        data[500] = SILENCE_PEAK_THRESHOLD + 50
        self.assertFalse(_audio_is_silent(data))

    def test_custom_threshold(self):
        data = np.full(10, 100, dtype=np.int16)
        self.assertTrue(_audio_is_silent(data, threshold=101))
        self.assertFalse(_audio_is_silent(data, threshold=99))


# ---------------------------------------------------------------------------
# 4. _is_silence_hallucination
# ---------------------------------------------------------------------------

class TestHallucination(unittest.TestCase):

    def test_known_hallucinations(self):
        for phrase in ("תודה", "שלום", "כן", "thank you", "thanks", "bye"):
            with self.subTest(phrase=phrase):
                self.assertTrue(_is_silence_hallucination(phrase))

    def test_hallucination_with_trailing_punctuation(self):
        self.assertTrue(_is_silence_hallucination("תודה."))
        self.assertTrue(_is_silence_hallucination("שלום!"))

    def test_hallucination_with_surrounding_spaces(self):
        self.assertTrue(_is_silence_hallucination("  שלום  "))

    def test_empty_string_is_hallucination(self):
        self.assertTrue(_is_silence_hallucination(""))

    def test_whitespace_only_is_hallucination(self):
        self.assertTrue(_is_silence_hallucination("   "))

    def test_real_transcript_not_hallucination(self):
        self.assertFalse(_is_silence_hallucination("עכשיו אני בודק 1 2 3"))

    def test_partial_match_is_not_hallucination(self):
        # "תודה" is a hallucination, but "תודה רבה על העזרה" is real speech
        self.assertFalse(_is_silence_hallucination("תודה רבה על העזרה"))

    def test_english_real_sentence_not_hallucination(self):
        self.assertFalse(_is_silence_hallucination("open the file please"))


# ---------------------------------------------------------------------------
# 5. _prune_recordings
# ---------------------------------------------------------------------------

class TestPruneRecordings(unittest.TestCase):

    def _make_dirs(self, base: Path, names: list[str]) -> list[Path]:
        dirs = []
        for name in names:
            d = base / name
            d.mkdir(parents=True)
            (d / "audio.wav").write_bytes(b"fake")
            dirs.append(d)
        return dirs

    def test_prune_keeps_newest(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            self._make_dirs(base, [
                "diburit_20240101_000000_000000",
                "diburit_20240102_000000_000000",
                "diburit_20240103_000000_000000",
                "diburit_20240104_000000_000000",
            ])
            _prune_recordings(2, recordings_dir=base)
            remaining = sorted(p.name for p in base.iterdir() if p.is_dir())
            self.assertEqual(remaining, [
                "diburit_20240103_000000_000000",
                "diburit_20240104_000000_000000",
            ])

    def test_prune_zero_does_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            self._make_dirs(base, ["diburit_20240101_000000_000000"])
            _prune_recordings(0, recordings_dir=base)
            self.assertEqual(len(list(base.iterdir())), 1)

    def test_prune_nonexistent_dir_does_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does_not_exist"
            _prune_recordings(5, recordings_dir=missing)  # should not raise

    def test_non_diburit_dirs_not_pruned(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "other_dir").mkdir()
            self._make_dirs(base, [
                "diburit_20240101_000000_000000",
                "diburit_20240102_000000_000000",
            ])
            _prune_recordings(1, recordings_dir=base)
            # other_dir must survive
            self.assertTrue((base / "other_dir").exists())


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
