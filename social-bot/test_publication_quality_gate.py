#!/usr/bin/env python3
"""Fail-closed X publication quality gate regression tests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
from publish import review_allows_new_queue, main


class QualityGateTests(unittest.TestCase):
    def test_missing_approval_is_closed(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(review_allows_new_queue(Path(td)/"missing.json"))

    def test_invalid_approval_is_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"gate.json"
            path.write_text("not-json",encoding="utf-8")
            self.assertFalse(review_allows_new_queue(path))

    def test_explicit_hold_is_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"gate.json"
            path.write_text(json.dumps({"new_reservations_enabled":False}),encoding="utf-8")
            self.assertFalse(review_allows_new_queue(path))

    def test_only_true_reopens(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"gate.json"
            path.write_text(json.dumps({"new_reservations_enabled":"true"}),encoding="utf-8")
            self.assertFalse(review_allows_new_queue(path))
            path.write_text(json.dumps({"new_reservations_enabled":True}),encoding="utf-8")
            self.assertTrue(review_allows_new_queue(path))

    def test_on_hold_never_contacts_buffer(self):
        with patch.dict("os.environ",{"ENABLE_X_BOT":"true","BUFFER_API_KEY":"secret"}):
            with patch("publish.review_allows_new_queue",return_value=False), patch("publish.choose_x_channel") as channel:
                self.assertEqual(main(),0)
                channel.assert_not_called()


if __name__=="__main__":
    unittest.main(verbosity=2)
