import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from .editorial import audit, GENERATION_ORDER
from .guards import accepted,review
from .daily import generate,SECTIONS
from .test_visuals import stories
from .visuals import plan

TEXT = '왜 조건이 다른가요? 쉽게 말하면 적용 범위가 다릅니다. 그래서 대상부터 확인해야 합니다.'
SOURCE = '적용 범위가 다르므로 대상 여부를 확인해야 한다.'


def passing():
    return dict(score=98,facts_ok=True,dates_ok=True,balance_ok=True,safety_ok=True,
                issues=[],findings=[],editorial=dict(question_quote='왜 조건이 다른가요?',
                explanation_quote='쉽게 말하면 적용 범위가 다릅니다.',
                payoff_quote='그래서 대상부터 확인해야 합니다.',source_quote=SOURCE,
                question_answered=True,adds_understanding=True,non_repetitive=True,reason='조건과 답을 설명함'))


class EditorialTests(unittest.TestCase):
    def test_literal_proof_and_empty_payoff_fail(self):
        report=passing()
        self.assertEqual(audit(report,TEXT,SOURCE),[])
        self.assertTrue(accepted(report))
        report['editorial']['payoff_quote']=''
        self.assertEqual(audit(report,TEXT,SOURCE),[])
        self.assertFalse(accepted(report))
        report['editorial']['source_quote']='실제로 존재하지 않는 인용입니다'
        self.assertTrue(audit(report,TEXT,SOURCE))

    def test_factual_success_does_not_mask_boring_script(self):
        report=passing()
        report['editorial']['adds_understanding']=False
        writer=Mock()
        writer.json.return_value=report
        result=review(writer,TEXT,SOURCE)
        self.assertFalse(accepted(result))
        self.assertEqual(writer.json.call_count,1)
        self.assertTrue(result['facts_ok'])

    def test_title_does_not_require_longform_story_structure(self):
        writer=Mock()
        value=passing()
        value.pop('editorial')
        writer.json.return_value=value
        self.assertTrue(accepted(review(writer,'제목',SOURCE,kind='title')))

    def test_core_first_with_same_seven_writing_calls(self):
        writer=Mock()
        prompts=[]
        def ask(prompt,limit):
            prompts.append(prompt)
            n=int(re.search(r'본문만 공백 기준 (\d+)어절',prompt)[1])
            return ' '.join(['설명합니다.']*n)
        writer.ask.side_effect=ask
        with tempfile.TemporaryDirectory() as tmp,patch('longform_system.daily.validate'),patch('longform_system.daily.review',return_value=passing()):
            script,_=generate(writer,stories(),Path(tmp)/'script.md')
        self.assertEqual(len(prompts),7)
        for prompt,i in zip(prompts,GENERATION_ORDER):
            self.assertIn("지금은 '"+SECTIONS[i][1]+"'",prompt)
        self.assertIn('설명합니다.',prompts[3])
        self.assertEqual(script.count('[SHORTS_HOOK]'),3)

    def test_explanation_foreground_is_verbatim(self):
        line='쉽게 말하면 적용 조건을 먼저 확인해야 합니다.'
        board=plan([('핵심 1','첫 문장입니다.'),('핵심 1',line)],stories())
        row=board['scenes'][1]
        self.assertEqual(row['kind'],'explanation')
        self.assertEqual(row['explanation'],line)
        self.assertEqual(row['visual_priority'],'foreground')
