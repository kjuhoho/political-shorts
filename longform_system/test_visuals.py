import hashlib
import json
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path

from .visuals import plan, load_media, video_filter, credits, draw_scene


def stories():
    return [dict(headline=f'이슈 {i}', sources=[dict(name='예시 출처',url=f'https://example.org/{i}',
        published='2026-09-23',title=f'이슈 {i}',body='확인된 원문입니다. 다른 문장입니다.')]) for i in range(3)]


class VisualTests(unittest.TestCase):
    def test_source_mapping_not_global_keyword(self):
        result = plan([('핵심 3','국회에서 논의했습니다.'),('훅','세 이슈입니다.')],stories())
        self.assertEqual(result['scenes'][0]['source']['url'],'https://example.org/2')
        self.assertIsNone(result['scenes'][1]['source'])
        self.assertEqual(result['media_scenes'],0)

    def test_literal_excerpt_and_full_number_context(self):
        result = plan([('핵심 1','확인합니다.'),('핵심 1','100명이라는 주장은 확인되지 않았습니다.')],stories())
        self.assertEqual(result['scenes'][0]['excerpt'],'확인된 원문입니다.')
        self.assertEqual(result['scenes'][1]['kind'],'number')
        self.assertIn('확인되지',result['scenes'][1]['narration'])

    def test_wrong_rights_digest_and_path_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root/'photo.png'
            asset.write_bytes(b'fixture')
            registry = root/'media-registry.json'
            row = dict(reviewed=True,license='CC-BY-4.0',credit='Author',
                source_url='https://example.org/0',asset_page='https://example.org/photo',
                anchor='국회에서',caption='자료 사진 · 촬영일 미상',path='photo.png',license_url='https://creativecommons.org/licenses/by/4.0/',
                sha256=hashlib.sha256(b'fixture').hexdigest())
            for change in ({'reviewed':False},{'license':'unknown'},{'sha256':'wrong'},
                           {'path':'../outside.png'},{'source_url':'https://example.org/unrelated'}):
                registry.write_text(json.dumps([{**row,**change}]))
                with self.assertRaises(ValueError):
                    load_media(registry,stories())
            registry.write_text(json.dumps([row]))
            matched = plan([('핵심 1','첫 문장입니다.'),('핵심 1','국회에서 논의했습니다.'),('핵심 2','국회에서 논의했습니다.')],stories(),registry)
            self.assertEqual(matched['scenes'][1]['kind'],'media')
            self.assertNotEqual(matched['scenes'][2]['kind'],'media')

    def test_no_extra_generation_or_subtitle_zoom(self):
        graph = video_filter(100)
        self.assertIn('[art][1:v]overlay',graph)
        self.assertEqual(graph.count('zoompan'),1)
        self.assertNotIn('zoompan',video_filter(100,True))

    def test_library_requires_evidence_and_preserves_historical_credit(self):
        rows = stories()
        rows[0]['sources'][0]['body'] = '국회 본회의에서 논의했습니다.'
        result = plan([('핵심 1','첫 문장입니다.'),('핵심 1','국회 본회의에서 논의했습니다.')],rows)
        self.assertEqual(result['scenes'][1]['kind'],'media')
        attribution = credits(result['scenes'])
        self.assertIn('2007-12-12',attribution)
        self.assertIn('KOGL-1',attribution)
        self.assertIn('commons.wikimedia.org',attribution)
        rows[0]['sources'][0]['body'] += ' 일본 사례입니다.'
        excluded = plan([('핵심 1','첫 문장입니다.'),('핵심 1','국회 본회의에서 논의했습니다.')],rows)
        self.assertNotEqual(excluded['scenes'][1]['kind'],'media')

    def test_real_ffmpeg_still_and_video_compositing(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                self.skipTest('FFmpeg unavailable')
        font = Path(__file__).resolve().parents[1]/'assets/fonts/DoHyeon-Regular.ttf'
        rows = stories()
        rows[0]['sources'][0]['body'] = '국회 본회의에서 논의했습니다.'
        board = plan([('핵심 1','첫 문장입니다.'),('핵심 1','국회 본회의에서 논의했습니다.')],rows)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            png, overlay = root/'art.png', root/'overlay.png'
            draw_scene(board['scenes'][1],png,overlay,font)
            for video in (False,True):
                picture = (['-f','lavfi','-i','color=c=blue:s=640x360:r=30'] if video else
                           ['-loop','1','-i',str(png)])
                result = subprocess.run([ffmpeg,'-v','error',*picture,'-loop','1','-i',str(overlay),
                    '-filter_complex',video_filter(15,video),'-map','[v]','-frames:v','15',
                    '-f','null','-'],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':
    unittest.main()
