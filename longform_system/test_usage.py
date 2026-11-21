import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from .llm import Writer
from groq import APIConnectionError
import httpx


class UsageGuards(unittest.TestCase):
    @patch('longform_system.llm.time.sleep')
    def test_connection_retry_is_bounded_and_keeps_unknown_usage(self, _):
        with tempfile.TemporaryDirectory() as folder:
            w = self.writer(folder)
            w.client.chat.completions.create.side_effect = APIConnectionError(
                request=httpx.Request('POST', 'https://example.invalid'))
            with self.assertRaises(APIConnectionError):
                w.ask('prompt')
            self.assertEqual(w.calls, 2)
            self.assertEqual(w.connection_retries, 1)
            self.assertEqual(w.uncertain_tokens, 2*w.estimate('prompt', 1600))

    @patch('longform_system.llm.time.sleep')
    def test_successful_retry_does_not_repeat_completed_requests(self, _):
        with tempfile.TemporaryDirectory() as folder:
            w = self.writer(folder)
            success = w.client.chat.completions.create.return_value
            w.client.chat.completions.create.side_effect = [APIConnectionError(
                request=httpx.Request('POST', 'https://example.invalid')), success]
            self.assertEqual(w.ask('prompt'), 'answer')
            self.assertEqual(w.ask('prompt'), 'answer')
            self.assertEqual(w.calls, 2)
            self.assertEqual(w.tokens, 100)
            self.assertGreater(w.uncertain_tokens, 0)

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

    @patch('longform_system.llm.time.sleep')
    def test_review_retry_fits_where_the_byte_reserve_refused_it(self, _):
        # 2026-11-20: 10 calls / 39,569 tokens used, then a review retry with a ~12.5 KB Korean prompt was refused
        # ("API budget exhausted") although a review really costs ~3.8k tokens. The old reserve counted every byte
        # as a token: 12,498 + 1,024 + 2,000 = 15,522 > the 15,431 left.
        with tempfile.TemporaryDirectory() as folder:
            w = self.writer(folder)
            w.model = 'openai/gpt-oss-120b'
            w.calls, w.tokens = 10, 39569
            prompt = '가' * 4166                                   # 12,498 UTF-8 bytes
            self.assertGreater(39569 + len(prompt.encode()) + 1024 + 2000, 55000)   # the old rule refused it
            self.assertEqual(w.ask(prompt, 2000), 'answer')
            w.client.chat.completions.create.assert_called_once()
            self.assertEqual(w.uncertain_tokens, 0)

    def test_cap_still_stops_a_call_that_could_exceed_it(self):
        with tempfile.TemporaryDirectory() as folder:
            w = self.writer(folder)
            w.model = 'openai/gpt-oss-120b'
            w.calls, w.tokens = 12, 50000
            with self.assertRaises(RuntimeError):
                w.ask('가' * 4166, 2000)                           # 50,000 + 6,678 > 55,000
            w.client.chat.completions.create.assert_not_called()
            w.calls, w.tokens = Writer.MAX_CALLS, 0                  # the call cap is independent of tokens
            with self.assertRaises(RuntimeError):
                w.ask('short', 100)

    def test_estimate_is_still_conservative_for_korean_text(self):
        # measured: ~3.6 UTF-8 bytes per token on gpt-oss; the estimate must stay above the real input cost
        w = object.__new__(Writer)
        w.model = 'openai/gpt-oss-120b'
        measured_tokens = 12500 / 3.6
        self.assertGreater(w.estimate('가' * 4166, 0) - 512, measured_tokens)
        w.model = 'llama-3.3-70b-versatile'
        self.assertEqual(w.estimate('가' * 4166, 2000), 4166 * 3 // 2 + 512 + 2000)   # weaker Korean tokenizer: bytes/2

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
