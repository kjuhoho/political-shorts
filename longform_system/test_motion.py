import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .motion import frame, picture_input, THEMES, select_theme
from .visuals import plan, credits, video_filter, draw_scene
from .test_visuals import stories


class MotionTests(unittest.TestCase):
    def test_motion_in_context_without_issue_headline(self):
        row = plan([('맥락','첫 문장입니다.'),('맥락','공급망을 살펴봅니다.')],stories())['scenes'][1]
        self.assertEqual(row['kind'],'motion')
        with tempfile.TemporaryDirectory() as tmp:
            draw_scene(row,Path(tmp)/'art.png',Path(tmp)/'overlay.png',Path('assets/fonts/DoHyeon-Regular.ttf'))

    def test_grounded_topic_selection_and_disclosure(self):
        board = plan([('핵심 1','첫 문장입니다.'),('핵심 1','공급망 협력을 논의했습니다.'),
                      ('핵심 2','첫 문장입니다.'),('핵심 2','다른 논의를 했습니다.')],stories())
        self.assertEqual(board['scenes'][1]['motion'],'supply-chain')
        self.assertIsNone(board['scenes'][1]['media'])
        self.assertNotIn('illustration',board['scenes'][1])
        self.assertEqual(board['scenes'][3]['motion'],'evidence')
        self.assertIn('모션그래픽',credits(board['scenes']))
        self.assertEqual(board['motion_scenes'],2)

    def test_all_ordinary_scenes_move_and_recap_is_preserved(self):
        board = plan([('훅','오늘 소식입니다.'),('맥락','배경입니다.'),
                      ('시사점','내일 확인합니다.'),('요약+예고','세 가지입니다.'),
                      ('요약+예고','후속을 확인합니다.')],stories())
        self.assertEqual([r['kind'] for r in board['scenes']],['motion']*3+['overview','motion'])
        self.assertTrue(all(r['visual_priority']=='background' for r in board['scenes'] if r['kind']=='motion'))

    def test_every_theme_moves_and_unknown_falls_back(self):
        font = Path('assets/fonts/DoHyeon-Regular.ttf')
        self.assertEqual(select_theme('무관한 말입니다.'),'evidence')
        for theme in THEMES:
            a,b = frame(0,font,theme),frame(2,font,theme)
            self.assertNotEqual(a.tobytes(),b.tobytes(),theme)
            self.assertEqual(a.crop((0,765,1920,1080)).tobytes(),b.crop((0,765,1920,1080)).tobytes())
        with self.assertRaises(ValueError):
            frame(0,font,'unknown')

    def test_objects_move_without_moving_subtitle_area(self):
        font = Path('assets/fonts/DoHyeon-Regular.ttf')
        a,b = frame(0,font),frame(3,font)
        self.assertEqual(a.size,(1920,1080))
        self.assertNotEqual(a.crop((100,230,1170,710)).tobytes(),b.crop((100,230,1170,710)).tobytes())
        self.assertEqual(a.crop((0,765,1920,1080)).tobytes(),b.crop((0,765,1920,1080)).tobytes())

    def test_full_frame_filter_and_cached_clip(self):
        self.assertNotIn('scale=1680',video_filter(240,True,True))
        with tempfile.TemporaryDirectory() as tmp:
            with patch('longform_system.motion.render_art') as render:
                render.side_effect=lambda path,*args: path.write_bytes(b'fixture')
                for _ in range(2):
                    inputs,video,full = picture_input({'motion':'supply-chain'},None,tmp,None,offset=10.5)
                    self.assertTrue(video and full)
                    self.assertEqual(inputs[inputs.index('-ss')+1],'2.5')
                self.assertEqual(render.call_count,1)
