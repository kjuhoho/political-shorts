import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from longform_system.guards import accepted, grounded, ground_quote, review, publication_reviews_ok
from longform_system.ledger import Ledger
from longform_system.renderer import clean_script
from longform_system.research import blocked

# 2026-11-21 (run 44919004464): the reviewer found a REAL date error but "completed" its source quote with a date
# prefix that is not in the article, so the finding was thrown out as ungrounded and the day failed.
SOURCE = ('(서울=연합뉴스) 김정진 기자 = 국회 예산결산특별위원회 예산안조정소위는 21일 내년도 지역사랑상품권 예산을 '
          '놓고 공방을 벌인 끝에 심사를 일단 보류키로 했다. 더불어민주당은 정부 원안 유지 필요성을 강조했다.')
SCRIPT = '2026년 11월 22일, 국회 예산소위가 내년도 지역사랑상품권 예산을 심사 보류한 뒤 양당이 격돌했습니다.'
PADDED = ('2026년 11월 21일, 국회 예산결산특별위원회 예산안조정소위는 21일 내년도 지역사랑상품권 예산을 '
          '놓고 공방을 벌인 끝에 심사를 일단 보류키로 했다.')


def failing_review(source_quote):
    return dict(score=78, facts_ok=True, dates_ok=False, balance_ok=True, safety_ok=True,
                issues=['사건일 불일치'], findings=[dict(
                    script_quote='2026년 11월 22일, 국회 예산소위가 내년도 지역사랑상품권 예산을 심사 보류한 뒤',
                    source_quote=source_quote, reason='대본은 22일, 원문은 21일')])


class QuoteGrounding(unittest.TestCase):
    def test_real_finding_survives_a_quote_padded_with_words_not_in_the_source(self):
        report = failing_review(PADDED)
        writer = Mock()
        writer.json.return_value = dict(report, editorial=dict(
            question_quote='', explanation_quote='', payoff_quote='', source_quote='',
            question_answered=False, adds_understanding=False, non_repetitive=True, reason='x'))
        result = review(writer, SCRIPT, SOURCE)                    # used to raise "did not ground its findings"
        self.assertEqual(writer.json.call_count, 1)
        quote = result['findings'][0]['source_quote']
        self.assertIn(quote, SOURCE)                               # now literal, so the repair step gets real evidence
        self.assertNotIn('2026년 11월 21일,', quote)
        self.assertEqual(result['quotes_trimmed'], ['findings[0].source_quote'])
        self.assertFalse(accepted(result))                         # the date error itself still blocks the script

    def test_exact_quotes_are_returned_unchanged(self):
        exact = '국회 예산결산특별위원회 예산안조정소위는 21일 내년도'
        self.assertEqual(ground_quote(exact, SOURCE), exact)

    def test_invented_or_short_quotes_are_still_rejected(self):
        self.assertIsNone(ground_quote('원문 어디에도 없는 문장입니다. 전혀 다른 내용입니다.', SOURCE))
        self.assertIsNone(ground_quote('예산을 놓고', 'x ' + SOURCE[:5]))                  # under 4 usable characters of proof
        # only a 22-character stretch is real, inside a 120-character claim: below the 50% share
        claim = '국회 예산결산특별위원회 예산안조정소위는 21일 ' + '전혀 다른 내용을 길게 덧붙여 만든 문장입니다. ' * 5
        self.assertIsNone(ground_quote(claim, SOURCE))
        # a real stretch under 20 characters is not enough
        self.assertIsNone(ground_quote('내년도 지역사랑상품권 예산 그리고 없는 말', SOURCE))

    def test_an_ungrounded_review_still_fails_after_two_attempts(self):
        writer = Mock()
        writer.json.return_value = failing_review('원문에 전혀 없는 인용문입니다. 지어낸 문장입니다.')
        with self.assertRaises(RuntimeError):
            review(writer, SCRIPT, SOURCE)
        self.assertEqual(writer.json.call_count, 2)


class PublicationGuards(unittest.TestCase):
    def test_bad_quote_feedback_is_specific_and_still_blocks(self):
        writer = Mock()
        writer.json.return_value = dict(score=30, facts_ok=False, findings=[dict(
            script_quote='없는 대본 문장', source_quote='없는 원문 문장', reason='주장')])
        with self.assertRaises(RuntimeError):
            review(writer, '실제 대본입니다.', '실제 원문입니다.')
        correction_prompt = writer.json.call_args_list[1].args[0]
        self.assertIn('findings[0].script_quote', correction_prompt)
        self.assertIn('findings[0].source_quote', correction_prompt)

    def test_review_prompt_ends_with_one_complete_output_shape(self):
        # 2026-11-20: a passing review was rejected only because the model returned the rubric's base shape and
        # left out the separately described `editorial` object; the retry then hit the budget.
        writer = Mock()
        writer.json.return_value = dict(score=98, facts_ok=True, dates_ok=True, balance_ok=True, safety_ok=True,
                                        issues=[], findings=[], editorial=dict(
                                            question_quote='', explanation_quote='', payoff_quote='', source_quote='',
                                            question_answered=False, adds_understanding=False,
                                            non_repetitive=False, reason='x'))
        review(writer, '대본입니다.', '원문입니다.')
        script_prompt = writer.json.call_args.args[0]
        tail = script_prompt[script_prompt.rindex('[최종 출력 형식]'):]
        for key in ('score', 'facts_ok', 'dates_ok', 'balance_ok', 'safety_ok', 'issues', 'findings', 'editorial'):
            self.assertIn(key, tail)
        self.assertNotIn('source_checks', tail)               # only when overseas sources are in the bundle
        review(writer, '제목', '원문입니다.', kind='title')
        title_tail = writer.json.call_args.args[0]
        title_tail = title_tail[title_tail.rindex('[최종 출력 형식]'):]
        self.assertNotIn('editorial', title_tail)             # titles are not held to the story structure
        bundle = dict(sources=[dict(language='ko'), dict(language='en')])
        review(writer, '대본입니다.', '원문입니다.', source_bundle=bundle)
        overseas = writer.json.call_args.args[0]
        self.assertIn('source_checks', overseas[overseas.rindex('[최종 출력 형식]'):])

    def test_partial_reviews_cannot_authorize_publication(self):
        passing = dict(score=100, facts_ok=True, dates_ok=True, balance_ok=True,
                       safety_ok=True, issues=[])
        for count in (0, 1, 2, 3, 5):
            self.assertFalse(publication_reviews_ok(dict(quality=[passing]*count, title_review=passing)))
        self.assertTrue(publication_reviews_ok(dict(quality=[passing]*4, title_review=passing)))
        self.assertFalse(publication_reviews_ok(dict(quality=[passing]*4)))

    def test_unsupported_review_never_triggers_repair(self):
        report = dict(score=30, facts_ok=False, dates_ok=True, balance_ok=True,
                      safety_ok=True, issues=['pretrained claim'], findings=[])
        writer = Mock()
        writer.json.return_value = report
        with self.assertRaises(RuntimeError):
            review(writer, '이재명 대통령은 제안했습니다.', '이재명 대통령은 제안했습니다.')
        self.assertEqual(writer.json.call_count, 2)

    def test_review_quotes_must_exist_in_both_inputs(self):
        report = dict(score=30, facts_ok=False, findings=[dict(
            script_quote='유엔 사무국', source_quote='유엔사와 협의', reason='기관이 변경됨')])
        self.assertTrue(grounded(report, '유엔 사무국과 조사', '유엔사와 협의 중입니다.'))
        self.assertFalse(grounded(report, '유엔군사령부와 조사', '유엔사와 협의 중입니다.'))

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

    def test_short_sentences_are_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'script.md'
            path.write_text('## 4:20–5:00 | 요약 + 예고\n첫째. 아직 미정입니다. 후속 발표를 확인하겠습니다.', encoding='utf-8')
            chunks = clean_script(path)
        self.assertEqual(' '.join(text for _, text in chunks),
                         '첫째. 아직 미정입니다. 후속 발표를 확인하겠습니다.')


if __name__ == '__main__':
    unittest.main()
