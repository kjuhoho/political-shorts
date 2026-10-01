import copy
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from .international import enrich, concepts, cross_match, english_query, record_event_reviews, event_review_valid
from .media_collect import approve
from .research import evidence

TITLE = '한국 호주 공급망 정상회담'
ENGLISH = 'South Korea Australia supply chain summit'
STORY = dict(headline=TITLE,sources=[dict(name='국내 보도',title=TITLE,
    body=(TITLE+' 관련 원문입니다. ')*60,url='https://example.org/korea',published='2026-10-01T10:00:00+00:00')])


class FakeFetch:
    def __init__(self):
        self.requests = self.bytes = 0

    def get(self,url,limit,**kwargs):
        self.requests += 1
        if 'rss' in url:
            publisher = 'press.test' if 'press.test' in url else 'official.test'
            data = f'''<rss version="2.0"><channel><item><title>{ENGLISH}</title>
                <link>https://{publisher}/story</link><pubDate>Thu, 01 Oct 2026 09:00:00 GMT</pubDate>
                </item></channel></rss>'''.encode()
        else:
            data = ('<article>'+ENGLISH*80+'</article>').encode()
        self.bytes += len(data)
        return data


class InternationalTests(unittest.TestCase):
    def test_cross_language_needs_specific_topic_not_country_alone(self):
        self.assertTrue(cross_match(TITLE,ENGLISH))
        self.assertFalse(cross_match('한국 호주 무역','South Korea Australia election'))
        self.assertFalse(cross_match('호주','Australia'))
        self.assertNotIn('entity:un',concepts('유엔사 발표'))
        self.assertNotIn('entity:un',concepts('United Nations Command'))
        self.assertIn('Australia',english_query(TITLE))

    def test_source_enrichment_is_candidate_not_confirmation_and_cached(self):
        feeds = [dict(name='Foreign press',kind='foreign_press',url='https://press.test/rss',hosts=('press.test',)),
                 dict(name='Official',kind='official_government',url='https://official.test/rss',hosts=('official.test',))]
        with tempfile.TemporaryDirectory() as tmp, patch('longform_system.international.FEEDS',feeds), \
             patch('longform_system.international.trafilatura.extract',return_value=(ENGLISH+' original text. ')*40):
            f = FakeFetch()
            stamp = datetime(2026,10,1,12,tzinfo=timezone.utc)
            enriched = enrich([STORY],tmp,f,stamp)
            self.assertEqual(len(enriched[0]['sources']),3)
            self.assertEqual(len(STORY['sources']),1)
            foreign = enriched[0]['sources'][1:]
            self.assertEqual({s['kind'] for s in foreign},{'foreign_press','official_government'})
            self.assertTrue(all(s['cross_check_status']=='candidate_not_confirmation' for s in foreign))
            self.assertTrue(all('news.google.com' not in s['url'] for s in foreign))
            count = f.requests
            enrich([STORY],tmp,f,stamp)
            self.assertEqual(f.requests,count)
            audit = json.loads((Path(tmp)/'international-research.json').read_text(encoding='utf-8'))
            self.assertTrue(audit['google_discovery'])

    def test_stale_feed_never_becomes_evidence(self):
        feeds = [dict(name='Old',kind='foreign_press',url='https://press.test/rss',hosts=('press.test',))]
        with tempfile.TemporaryDirectory() as tmp, patch('longform_system.international.FEEDS',feeds):
            enriched = enrich([STORY],tmp,FakeFetch(),datetime(2026,11,1,tzinfo=timezone.utc))
            self.assertEqual(len(enriched[0]['sources']),1)

    def test_four_sources_fit_evidence_budget_without_hiding_last(self):
        story = copy.deepcopy(STORY)
        story['sources'] = [{**story['sources'][0],'name':f'출처{i}','body':f'원문{i} '+('본문 '*1000)} for i in range(4)]
        for budget in (1600,2000,4000):
            result = evidence(story,budget)
            self.assertLessEqual(len(result),budget)
            for i in range(4):
                self.assertIn(f'원문{i}',result)

    def test_foreign_media_needs_licence_and_exact_original_even_with_translation(self):
        source = {**STORY['sources'][0],'title':ENGLISH}
        c = dict(url='https://example.org/a.jpg',asset_page=source['url'],source_url=source['url'],
            article_link=source['url'],license_url='https://creativecommons.org/licenses/by/4.0/',
            credit='Photographer',description=ENGLISH,captured='2026-10-01')
        self.assertIsNotNone(approve(c,source,[TITLE])[0])
        self.assertIsNone(approve({**c,'article_link':'https://wrong.example'},source,[TITLE])[0])
        self.assertIsNone(approve({**c,'license_url':''},source,[TITLE])[0])

    def test_event_review_needs_literal_both_sides_and_never_reuses_stale_check(self):
        story = copy.deepcopy(STORY)
        foreign = dict(name='Foreign',language='en',title=ENGLISH,body=ENGLISH*20,
                       url='https://example.org/en',published=story['sources'][0]['published'])
        story['sources'].append(foreign)
        check = dict(url=foreign['url'],same_event=True,source_quote=ENGLISH,
                     primary_quote=story['sources'][0]['body'][:35])
        record_event_reviews(story,dict(source_checks=[check]))
        self.assertTrue(event_review_valid(foreign,story['sources'][0]))
        record_event_reviews(story,dict(source_checks=[{**check,'source_quote':'invented quotation that is not present'}]))
        self.assertNotIn('event_review',foreign)


if __name__ == '__main__':
    unittest.main()
