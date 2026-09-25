import unittest
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import main


class MainTests(unittest.TestCase):
    def test_failures_return_nonzero(self):
        with patch("main.run", side_effect=RuntimeError("simulated failure")):
            self.assertEqual(main.main([]), 1)

    def test_default_is_dry_run_and_apply_is_explicit(self):
        with patch("main.run") as run:
            self.assertEqual(main.main([]), 0)
            self.assertFalse(run.call_args.args[0].apply)
            self.assertEqual(main.main(["--apply"]), 0)
            self.assertTrue(run.call_args.args[0].apply)

    def test_dry_run_reads_but_never_applies(self):
        google = Mock()
        google.find_list.return_value = {"id": "school"}
        google.tasks.return_value = []
        canvas = Mock()
        canvas.collect.return_value = []
        canvas.counts = {}
        with patch.dict(os.environ, {"CANVAS_AUTH_KEY": "fake"}, clear=True), \
                patch.dict(sys.modules, {"dotenv": SimpleNamespace(load_dotenv=Mock())}), \
                patch("google_tasks.authenticate"), \
                patch("google_tasks.GoogleTasks", return_value=google), \
                patch("canvas.CanvasClient", return_value=canvas), \
                patch("builtins.print"):
            self.assertEqual(main.main(["--dry-run"]), 0)
        google.tasks.assert_called_once_with("school")
        canvas.collect.assert_called_once()
        google.apply.assert_not_called()

    def test_fetch_failure_prevents_writes(self):
        google = Mock()
        google.find_list.return_value = {"id": "school"}
        google.tasks.return_value = []
        canvas = Mock()
        canvas.collect.side_effect = RuntimeError("page two failed")
        with patch.dict(os.environ, {"CANVAS_AUTH_KEY": "fake"}, clear=True), \
                patch.dict(sys.modules, {"dotenv": SimpleNamespace(load_dotenv=Mock())}), \
                patch("google_tasks.authenticate"), \
                patch("google_tasks.GoogleTasks", return_value=google), \
                patch("canvas.CanvasClient", return_value=canvas):
            self.assertEqual(main.main(["--apply"]), 1)
        google.apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
