"""Tests for the TTS-hook transcript matcher (_transcript_matches).

Guards the fuzzy match that decides whether a sent user message is the
preceding Diburit dictation: small post-paste edits (typo fixes, a corrected
word) must still match, while a message typed from scratch must not.

Run:
    $env:PYTHONIOENCODING="utf-8"
    .venv/Scripts/python -m unittest tests.test_tts_match -v   # Windows
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from tts_assistant import _transcript_matches


class TestTranscriptMatches(unittest.TestCase):
    def test_exact(self):
        s = "אני רוצה להגמיש את הטקסט"
        self.assertTrue(_transcript_matches(s, s))

    def test_whitespace_differences_ignored(self):
        self.assertTrue(_transcript_matches("שלום   עולם", "שלום עולם"))

    def test_single_word_corrected(self):
        self.assertTrue(_transcript_matches(
            "תוסיף אופציה של הרגול לפי נושאים",
            "תוסיף אופציה של תרגול לפי נושאים",
        ))

    def test_single_char_typo_fix(self):
        self.assertTrue(_transcript_matches(
            "אתה לא מכריל לי את התשובות",
            "אתה לא מקריא לי את התשובות",
        ))

    def test_user_appended_text(self):
        self.assertTrue(_transcript_matches(
            "מה קורה אם אני מסונכרן",
            "מה קורה אם אני מסונכרן? תענה בקצרה בבקשה",
        ))

    def test_typed_from_scratch_rejected(self):
        self.assertFalse(_transcript_matches(
            "אני רוצה להגמיש את הטקסט של ההקראה",
            "תפתח לי את הקובץ הראשי ותריץ את הבדיקות",
        ))

    def test_empty_transcript_rejected(self):
        self.assertFalse(_transcript_matches("", "משהו שנשלח"))

    def test_empty_user_text_rejected(self):
        self.assertFalse(_transcript_matches("טקסט שהוכתב", ""))


if __name__ == "__main__":
    unittest.main()
