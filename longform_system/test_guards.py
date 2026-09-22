import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from longform_system.guards import accepted
from longform_system.ledger import Ledger
from longform_system.renderer import clean_script
from longform_system.research import blocked


class PublicationGuards(unittest.TestCase):
    def test_missing_or_unsafe_review_never_passes(self):
        report = dict(score=100, facts_ok=True, dates_ok=True, balance_ok=True, safety_ok=True, issues=[])
        self.assertTrue(accepted(report))
        for field in report:
            copy = dict(report)
            del copy[field]
            self.assertFalse(accepted(copy))
        self.assertFalse(accepted({**report, 'safety_ok': False}))
        self.assertFalse(accepted({**report, 'score': 94}))

    def test_dmz_policy(self):
        self.assertTrue(blocked('DMZ 국제 영화제 예산 논란'))
        self.assertFalse(blocked('DMZ 평화 회담'))

    def test_uncertain_upload_reservation_blocks_second_insert(self):
        ledger = object.__new__(Ledger)
        ledger.get = Mock(return_value=({'status':'reserved'}, 'sha'))
        ledger.put = Mock()
        with self.assertRaises(RuntimeError):
            ledger.reserve('longform-2026-09-22', 'digest')
        ledger.put.assert_not_called()

    def test_reservation_failure_prevents_upload(self):
        ledger = object.__new__(Ledger)
        ledger.get = Mock(return_value=None)
        ledger.put = Mock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError):
            ledger.reserve('longform-2026-09-22', 'digest')

    def test_hook_narration_is_preserved_without_source_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'script.md'
            path.write_text('## 0:50–1:45 | 핵심 1\n[SHORTS_HOOK] 첫 문장은 음성에 반드시 포함됩니다.\n[화면 출처 텍스트: 매체 | URL]', encoding='utf-8')
            chunks = clean_script(path)
        self.assertEqual(chunks, [('핵심 1','첫 문장은 음성에 반드시 포함됩니다.')])


if __name__ == '__main__':
    unittest.main()
