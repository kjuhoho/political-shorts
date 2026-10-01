import io
import json
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from .media_collect import (approve, canonical, collect, commons_candidates,
                            schema_candidates, Fetcher, save_media)
from .visuals import load_media, plan

URL = 'https://example.org/news/123'
SOURCE = dict(url=URL,title='홍길동 호주 회담 공급망 협력',body='홍길동 호주 회담 공급망 협력 내용입니다.',
              name='예시 출처',published='2026-10-01T12:00:00+09:00')
NARRATION = '홍길동 호주 회담 공급망 협력 내용입니다.'


def candidate():
    return dict(origin='article-jsonld',url='https://example.org/photo.jpg',asset_page=URL,
        source_url=URL,article_link=URL,license_url='https://creativecommons.org/licenses/by/4.0/',
        credit='촬영자',description=NARRATION,captured='2026-10-01')


def article():
    c = candidate()
    return '<script type="application/ld+json">'+json.dumps({
        '@type':'NewsArticle','url':URL,'image':{'@type':'ImageObject',
        'contentUrl':c['url'],'license':c['license_url'],'creator':{'name':c['credit']},
        'caption':c['description'],'dateCreated':c['captured']}})+'</script>'


class FakeFetcher:
    def __init__(self):
        self.requests = self.bytes = 0

    def get(self,url,limit):
        self.requests += 1
        if 'w/api.php' in url:
            data = b'{"query":{"pages":{}}}'
        elif url.endswith('.jpg'):
            buf = io.BytesIO()
            Image.new('RGB',(800,450),'navy').save(buf,'JPEG')
            data = buf.getvalue()
        else:
            data = article().encode()
        self.bytes += len(data)
        return data


class CollectionTests(unittest.TestCase):
    def test_explicit_nested_license_and_capture_date(self):
        found = schema_candidates(article(),URL)
        verdict,reason = approve(found[0],SOURCE,[NARRATION])
        self.assertIsNone(reason)
        self.assertEqual(verdict['license'],'CC-BY-4.0')

    def test_page_license_and_og_are_not_asset_permission(self):
        document = '<meta property="og:image" content="https://example.org/a.jpg">'
        document += '<a rel="license" href="https://creativecommons.org/licenses/by/4.0/">license</a>'
        found = schema_candidates(document,URL)
        self.assertEqual(approve(found[0],SOURCE,[NARRATION])[1],'missing_or_unsupported_per_asset_license')

    def test_unrelated_article_in_sidebar_does_not_bind(self):
        document = article().replace('"url": "'+URL+'"','"url": "https://example.org/other"')
        found = schema_candidates(document,URL)
        self.assertEqual(approve(found[0],SOURCE,[NARRATION])[1],'no_exact_article_association')

    def test_ambiguous_candidates_fail_closed(self):
        mutations = [{'captured':''},{'captured':'2007-12-12'},{'captured':'2026-10-02'},
            {'article_link':'https://example.org/'},{'credit':''},{'description':'다른 인물의 사진'},
            {'description':NARRATION+' 자료사진'},{'license_url':'https://creativecommons.org/licenses/by-nc/4.0/'},
            {'license_url':'https://creativecommons.org/licenses/by-sa/4.0/'}]
        for changes in mutations:
            with self.subTest(changes=changes):
                self.assertIsNone(approve({**candidate(),**changes},SOURCE,[NARRATION])[0])
        self.assertIsNone(approve(candidate(),SOURCE,['무관한 내레이션입니다.'])[0])

    def test_upload_timestamp_is_not_capture_date(self):
        c = candidate()
        c.pop('captured')
        c['timestamp'] = '2026-10-01'
        self.assertIsNone(approve(c,SOURCE,[NARRATION])[0])

    def test_malformed_metadata_is_held_without_exception(self):
        self.assertIsNone(approve({**candidate(),'license_url':[]},SOURCE,[NARRATION])[0])
        self.assertEqual(canonical('https://[bad'), '')
        self.assertEqual(schema_candidates('<script type="application/ld+json">{"@type":null}</script>',URL),[])

    def test_commons_credit_link_must_be_exact(self):
        def data(link):
            ext = {'Credit':{'value':f'<a href="{link}">source</a>'},
                   'Artist':{'value':'촬영자'},'ImageDescription':{'value':NARRATION},
                   'LicenseUrl':{'value':candidate()['license_url']},'DateTimeOriginal':{'value':'2026-10-01'}}
            return {'query':{'pages':{'1':{'imageinfo':[{'url':'https://upload.wikimedia.org/a.jpg',
                        'descriptionurl':'https://commons.wikimedia.org/wiki/File:A.jpg','extmetadata':ext}]}}}}
        self.assertIsNotNone(approve(commons_candidates(data(URL),URL)[0],SOURCE,[NARRATION])[0])
        self.assertIsNone(approve(commons_candidates(data('https://example.org'),URL)[0],SOURCE,[NARRATION])[0])

    def test_collect_cache_integrity_and_visual_assignment(self):
        stories = [dict(headline=SOURCE['title'],sources=[SOURCE])]
        scenes = [('핵심 1','첫 문장입니다.'),('핵심 1',NARRATION)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = FakeFetcher()
            report = collect(stories,scenes,root,f)
            self.assertEqual(report['selected'],1)
            calls = f.requests
            self.assertEqual(collect(stories,scenes,root,f)['selected'],1)
            self.assertEqual(f.requests,calls)
            media = load_media(root/'auto-media-registry.json',stories)
            self.assertFalse(media[0]['reviewed'])  # Never forge human review.
            board = plan(scenes,stories*3,root/'media-registry.json')
            self.assertEqual(board['scenes'][1]['kind'],'media')
            Path(media[0]['resolved_path']).write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                load_media(root/'auto-media-registry.json',stories)

    def test_network_failure_falls_back_and_is_cached(self):
        class Failed(FakeFetcher):
            def get(self,url,limit):
                self.requests += 1
                raise OSError('offline')
        with tempfile.TemporaryDirectory() as tmp:
            f = Failed()
            report = collect([dict(headline=SOURCE['title'],sources=[SOURCE])],[],tmp,f)
            self.assertEqual(report['selected'],0)
            self.assertEqual(report['status'],'complete')
            self.assertEqual(len(report['candidates']),2)

    def test_private_hosts_and_redirects_are_not_followed(self):
        f = Fetcher()
        with patch('socket.getaddrinfo',return_value=[(0,0,0,'',('127.0.0.1',443))]):
            with self.assertRaisesRegex(ValueError,'non_public_host'):
                f.get('https://example.org',100)
        for url in ('http://example.org','https://user:secret@example.org','https://example.org:8080'):
            with self.assertRaisesRegex(ValueError,'unsafe_url'):
                f.get(url,100)

    def test_video_download_normalization_decodes_without_audio(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                self.skipTest('FFmpeg unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root/'fixture.mp4'
            subprocess.run([ffmpeg,'-y','-v','error','-f','lavfi','-i','color=blue:s=640x360:r=30',
                '-f','lavfi','-i','sine=frequency=440','-t','0.5','-c:v','libx264','-c:a','aac',
                str(fixture)],check=True)
            path,digest = save_media(fixture.read_bytes(),root/'download',ffmpeg)
            self.assertEqual(len(digest),64)
            check = subprocess.run([ffmpeg,'-v','info','-i',str(path),'-f','null','-'],
                                   capture_output=True,text=True)
            self.assertEqual(check.returncode,0)
            self.assertNotIn('Audio:',check.stderr)
            self.assertIn('1280x720',check.stderr)


if __name__ == '__main__':
    unittest.main()
