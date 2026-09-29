import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from .fish_audio import synthesize, validate_config, provider

CONFIG = dict(LONGFORM_TTS_PROVIDER='fish', LONGFORM_FISH_USE_ACK='true',
              LONGFORM_FISH_API_KEY='test-key', LONGFORM_FISH_VOICE_ID='own-voice')


class FishTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_default_stays_edge(self):
        self.assertEqual(provider(), 'edge')
        validate_config()

    @patch.dict(os.environ, {'LONGFORM_TTS_PROVIDER':'fish'}, clear=True)
    @patch('longform_system.fish_audio.requests.post')
    def test_unapproved_never_calls(self, post):
        with self.assertRaises(RuntimeError):
            synthesize('test', Path('unused.mp3'))
        post.assert_not_called()

    @patch.dict(os.environ, CONFIG, clear=True)
    @patch('longform_system.fish_audio.requests.post')
    def test_free_header_and_cache(self, post):
        post.return_value = Mock(status_code=200, content=b'ID3'+b'x'*128)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.mp3'
            synthesize('test', path)
            synthesize('test', path)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs['headers']['model'], 's2.1-pro-free')
        self.assertEqual(post.call_args.kwargs['json']['reference_id'], 'own-voice')

    @patch.dict(os.environ, CONFIG, clear=True)
    @patch('longform_system.fish_audio.requests.post')
    def test_failed_request_is_not_retried(self, post):
        post.return_value=Mock(status_code=402)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.mp3'
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    synthesize('test', path)
        self.assertEqual(post.call_count, 1)
