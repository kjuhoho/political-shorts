import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from .llm import Writer


class UsageGuards(unittest.TestCase):
    def writer(self, folder):
        w = object.__new__(Writer)
        w.model = 'test-model'
        w.calls = w.tokens = w.cache_hits = w.uncertain_tokens = 0
        w.last = 0
        w.cache = {}
        w.state_dir = Path(folder)
        w.client = Mock()
        w.client.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(total_tokens=100),
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='answer'))])
        return w

    @patch('longform_system.llm.time.sleep')
    def test_identical_input_calls_provider_once(self, _):
        with tempfile.TemporaryDirectory() as folder:
            w = self.writer(folder)
            self.assertEqual(w.ask('same'), w.ask('same'))
            self.assertEqual(w.calls, 1)
            self.assertEqual(w.cache_hits, 1)
            w.ask('changed')
            self.assertEqual(w.calls, 2)

    def test_budget_stops_before_provider_call(self):
        with tempfile.TemporaryDirectory() as folder:
            w = self.writer(folder)
            w.tokens = 54000
            with self.assertRaises(RuntimeError):
                w.ask('prompt')
            w.client.chat.completions.create.assert_not_called()

    @patch('longform_system.llm.time.sleep')
    def test_timeout_keeps_usage_and_reservation(self, _):
        with tempfile.TemporaryDirectory() as folder:
            w = self.writer(folder)
            w.client.chat.completions.create.side_effect = TimeoutError
            with self.assertRaises(TimeoutError):
                w.ask('prompt')
            self.assertEqual(w.calls, 1)
            self.assertGreater(w.uncertain_tokens, 0)
            self.assertTrue((Path(folder)/'usage.json').exists())
