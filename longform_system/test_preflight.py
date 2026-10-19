import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from .daily import generate
from .guards import validate_claims
from .renderer import prepare_audio
from .test_editorial import passing
from .test_visuals import stories


class PreflightTests(unittest.TestCase):
    def test_year_not_hidden_inside_number_or_korean_word(self):
        for text in ('2020년 사건', '지난2020 년 사건'):
            with self.assertRaisesRegex(RuntimeError, 'Unsupported year'):
                validate_claims(text, '번호 1202099. 기준일 2026년')
        validate_claims('2020년 사건', '2020년 당시 사건')

    def test_bad_year_corrected_once_before_remaining_generation(self):
        writer = Mock()
        calls = []
        def ask(prompt, limit):
            calls.append(prompt)
            if '기계 검사 오류:' in prompt:
                return ' '.join(['설명합니다.']*93)
            n = int(re.search(r'본문만 공백 기준 (\d+)어절', prompt)[1])
            return ('2020년 ' if len(calls)==1 else '')+' '.join(['설명합니다.']*n)
        writer.ask.side_effect = ask
        with tempfile.TemporaryDirectory() as tmp, patch('longform_system.daily.validate'), patch('longform_system.daily.review', return_value=passing()):
            script,_ = generate(writer, stories(), Path(tmp)/'script.md')
            self.assertTrue((Path(tmp)/'script.preflight.json').exists())
        self.assertIn('기계 검사 오류:', calls[1])
        self.assertEqual(len(calls),8)
        self.assertNotIn('2020년',script)

    def test_unfixed_year_stops_before_other_sections(self):
        writer = Mock()
        writer.ask.return_value = '2020년 사건입니다.'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'Unsupported year'):
                generate(writer,stories(),Path(tmp)/'script.md')
        self.assertEqual(writer.ask.call_count,2)

    def test_exact_audio_once_and_budget_before_calls(self):
        with patch('longform_system.renderer.make_audio',new_callable=AsyncMock) as audio, patch('longform_system.renderer.duration',side_effect=[10,12]):
            self.assertEqual(prepare_audio([('핵심 1','첫 문장'),('핵심 1','둘째 문장')],Path('.'),'voice'),22)
            self.assertEqual(audio.await_count,2)
            with self.assertRaises(ValueError):
                prepare_audio([('핵심 1','문장')]*61,Path('.'),'voice')
            self.assertEqual(audio.await_count,2)
