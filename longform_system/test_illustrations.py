import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import illustrations
from .illustrations import HOLD, TOPICS, build_prompt, pick, spec
from .test_visuals import stories
from . import register_illustrations as reg
from .visuals import draw_scene, plan, video_filter

# Words that appear in nearly every script: as keywords they would match every sentence.
TOO_GENERIC = {'보도', '정부', '정책', '발표', '대통령', '논란', '사건', '관련', '이번'}
FONT = Path(__file__).resolve().parents[1] / 'assets/fonts/DoHyeon-Regular.ttf'


def fake_library(*specs):
    return {name: dict(id=name, caption=name, keywords=list(words), neutral=neutral,
                       resolved_path=f'/fake/{name}.png')
            for name, words, neutral in specs}


class PackSpec(unittest.TestCase):
    def test_every_picture_has_a_caption_keywords_and_unique_id(self):
        rows = spec()
        self.assertGreaterEqual(len(rows), 30)
        for name, row in rows.items():
            self.assertTrue(row['caption'].strip(), name)
            self.assertLessEqual(len(row['caption']), 16, name)         # must stay on two short lines
            self.assertGreaterEqual(len(row['keywords']), 3, name)
            for word in row['keywords']:
                self.assertGreaterEqual(len(word), 2, (name, word))
                if not row.get('core'):          # the six originals keep their historical (motion) keywords
                    self.assertNotIn(word, TOO_GENERIC, (name, word))

    def test_core_keywords_match_the_motion_topics(self):
        for name, words in TOPICS.items():
            self.assertEqual(tuple(spec()[name]['keywords']), words, name)

    def test_new_pictures_carry_a_safe_prompt(self):
        pending = [row for row in spec().values() if not row.get('core')]
        self.assertGreaterEqual(len(pending), 24)
        for row in pending:
            prompt = build_prompt(row['subject'])
            self.assertIn(row['subject'].rstrip('.'), prompt)
            for clause in ('No people, no flags, no logos, no text, no numbers',
                           'not depicting an actual event', '16:9'):
                self.assertIn(clause, prompt, row['id'])
            self.assertNotRegex(row['subject'], r'(?i)\b(president|minister|politician|portrait|photo of)\b')

    def test_a_picture_without_artwork_is_never_selectable(self):
        # Spec rows wait for artwork; only hash-checked, inspected files enter the library, so a
        # keyword from a picture that does not exist yet can never select a missing file.
        library = illustrations.library()
        for name, row in spec().items():
            for word in row['keywords']:
                chosen = pick([f'{word}에 대해 논의합니다.'])
                self.assertIn(chosen['id'], library, (name, word))
                self.assertTrue(Path(chosen['resolved_path']).exists(), (name, word))


class RunPlanning(unittest.TestCase):
    def scenes(self, label, count, text='국회에서 법안을 논의합니다.'):
        return [(label, text)] * count

    def test_one_picture_is_held_for_at_most_three_sentences_of_one_section(self):
        # Literal numbers on purpose: a test that reads HOLD from the module cannot catch someone raising it.
        board = plan(self.scenes('맥락', 7) + self.scenes('시사점', 2), stories())
        runs = {}
        for row in board['scenes']:
            runs.setdefault(row['illustration_run'], []).append(row)
        self.assertEqual([len(rows) for rows in runs.values()], [3, 3, 1, 2])
        for rows in runs.values():
            self.assertEqual(len({r['label'] for r in rows}), 1)                       # never spans two sections
            self.assertEqual(len({r['illustration']['id'] for r in rows}), 1)          # one picture per run
        self.assertEqual(HOLD, 3)

    def test_special_cards_and_explicit_supply_chain_motion_are_untouched(self):
        board = plan([('핵심 1', '첫 문장입니다.'),                          # source card
                      ('핵심 1', '100명이 참석했습니다.'),                      # number card (every second scene)
                      ('핵심 1', '쉽게 말하면, 절차가 달라집니다.'),            # literal explanation
                      ('핵심 1', '공급망 점검을 논의했습니다.'),                # explicit motion rule
                      ('핵심 1', '국회에서 법안을 논의합니다.'),
                      ('요약 + 예고', '오늘의 세 가지 쟁점입니다.')], stories())      # first recap
        self.assertEqual([r['kind'] for r in board['scenes']],
                         ['source', 'number', 'explanation', 'motion', 'illustration', 'overview'])
        self.assertEqual(board['scenes'][3]['motion'], 'supply-chain')
        self.assertEqual(board['motion_scenes'], 1)
        self.assertEqual(board['illustration_scenes'], 1)

    def test_a_larger_pack_rotates_instead_of_looping(self):
        lib = fake_library(('a', ('국회',), False), ('b', ('국회',), False), ('c', ('국회',), False))
        with patch.object(illustrations, 'library', return_value=lib):
            used, last, seen = {}, None, []
            for _ in range(6):
                choice = pick(['국회에서 논의합니다.'], used=used, last=last)['id']
                used[choice] = used.get(choice, 0) + 1
                seen.append(choice)
                last = choice
        self.assertNotIn(True, [x == y for x, y in zip(seen, seen[1:])])      # never twice in a row
        self.assertEqual({seen.count(n) for n in 'abc'}, {2})                  # evenly used

    def test_relevance_beats_variety_when_only_one_picture_fits(self):
        lib = fake_library(('court', ('재판',), False), ('other', ('국회',), False))
        with patch.object(illustrations, 'library', return_value=lib):
            self.assertEqual(pick(['재판이 열렸습니다.'], used={'court': 3}, last='court')['id'], 'court')

    def test_unknown_topics_get_a_neutral_picture_never_an_unrelated_event(self):
        lib = fake_library(('court', ('재판',), False), ('desk', ('자료',), True))
        with patch.object(illustrations, 'library', return_value=lib):
            self.assertEqual(pick(['아무 관련 없는 이야기입니다.'])['id'], 'desk')

    def test_different_days_break_ties_differently(self):
        lib = fake_library(*[(n, ('국회',), False) for n in 'abcdef'])
        with patch.object(illustrations, 'library', return_value=lib):
            picks = {pick(['국회입니다.'], seed=seed)['id'] for seed in range(6)}
        self.assertEqual(len(picks), 6)

    def test_same_episode_always_plans_the_same_pictures(self):
        scenes = self.scenes('맥락', 5) + self.scenes('핵심 2', 5, '외교 회담을 논의합니다.')
        a = [r.get('illustration', {}).get('id') for r in plan(scenes, stories())['scenes']]
        b = [r.get('illustration', {}).get('id') for r in plan(scenes, stories())['scenes']]
        self.assertEqual(a, b)


def synthetic(path, size=(1672, 941), mode='RGB', flat=False):
    """A stand-in picture with real texture so the blank-picture check has something to measure."""
    from PIL import Image, ImageDraw
    im = Image.new(mode, size, (30, 60, 90) if mode == 'RGB' else (30, 60, 90, 255))
    if not flat:
        d = ImageDraw.Draw(im)
        for i in range(0, size[0], 40):
            d.rectangle((i, i * size[1] // size[0], i + 90, size[1]), fill=(200 - i % 150, 120, 60 + i % 90))
    im.save(path)
    return path


class Registration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(illustrations.ROOT, self.root, dirs_exist_ok=True)   # real originals + manifest + spec
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(illustrations.library.cache_clear)

    def test_validation_rejects_failed_generations(self):
        good = synthetic(self.root / 'ok.png')
        self.assertEqual(reg.problems(good), [])
        self.assertTrue(any('blank' in p for p in reg.problems(synthetic(self.root / 'flat.png', flat=True))))
        self.assertTrue(any('16:9' in p for p in reg.problems(synthetic(self.root / 'square.png', size=(1500, 1500)))))
        self.assertTrue(any('too small' in p for p in reg.problems(synthetic(self.root / 'small.png', size=(640, 360)))))
        self.assertTrue(any('transparent' in p for p in
                            reg.problems(self._transparent(self.root / 'alpha.png'))))
        (self.root / 'broken.png').write_bytes(b'not a png')
        self.assertTrue(any('cannot be opened' in p for p in reg.problems(self.root / 'broken.png')))
        self.assertEqual(reg.problems(self.root / 'nope.png'), ['missing file'])

    def _transparent(self, path):
        from PIL import Image
        im = synthetic(self.root / 'tmp-alpha.png').with_suffix('.png')
        with Image.open(im) as src:
            rgba = src.convert('RGBA')
            rgba.putalpha(Image.new('L', rgba.size, 90))
            rgba.save(path)
        return path

    def test_nothing_is_registered_without_an_explicit_approval(self):
        synthetic(self.root / 'court.png')
        self.assertEqual(reg.present(self.root), ['court'])
        self.assertEqual(reg.main(['--root', str(self.root), '--check']), 0)       # looking never registers
        self.assertNotIn('court', {row['id'] for row in reg.manifest(self.root)})

    def test_approved_pictures_enter_the_real_library_with_hash_and_prompt(self):
        synthetic(self.root / 'court.png')
        synthetic(self.root / 'budget.png')
        added = reg.register(self.root, ['court', 'budget'])
        self.assertEqual([row['id'] for row in added], ['court', 'budget'])
        rows = {row['id']: row for row in reg.manifest(self.root)}
        self.assertEqual(rows['court']['sha256'], hashlib.sha256((self.root / 'court.png').read_bytes()).hexdigest())
        self.assertTrue(rows['court']['visually_checked'])
        self.assertIn('courtroom', rows['court']['prompt'])
        library = illustrations.library(self.root)               # the same loader the daily run uses
        self.assertEqual({'court', 'budget'} - set(library), set())
        self.assertEqual(library['court']['caption'], '법원과 재판')
        self.assertNotIn('court', reg.pending(self.root))

    def test_bad_core_duplicate_or_failed_files_are_refused_and_change_nothing(self):
        before = (self.root / 'manifest.json').read_text(encoding='utf-8')
        synthetic(self.root / 'court.png', flat=True)
        for ids in (['evidence'], ['not-in-the-spec'], ['court'], ['budget']):     # core / unknown / blank / no file
            with self.assertRaises(ValueError):
                reg.register(self.root, ids)
        synthetic(self.root / 'court.png')
        reg.register(self.root, ['court'])
        with self.assertRaises(ValueError):
            reg.register(self.root, ['court'])                    # already registered
        self.assertNotEqual(before, (self.root / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))), 7)

    def test_a_changed_file_after_registration_is_caught_by_the_library(self):
        synthetic(self.root / 'court.png')
        reg.register(self.root, ['court'])
        synthetic(self.root / 'court.png', size=(1700, 956))      # regenerated without a new inspection
        with self.assertRaises(ValueError):
            illustrations.library(self.root)

    def test_prompt_file_lists_every_missing_picture_once(self):
        text = reg.prompts_markdown(self.root)
        for name in reg.pending(self.root):
            self.assertEqual(text.count(f'`{name}.png`'), 1, name)
        self.assertNotIn('`evidence.png`', text)
        self.assertIn('등록하지 말고', text)


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg unavailable')
class RealRender(unittest.TestCase):
    def test_artwork_moves_while_caption_and_subtitles_stay_still(self):
        from PIL import Image, ImageChops
        row = plan([('핵심 1', '첫 문장입니다.'), ('핵심 1', '국회에서 법안을 논의합니다.')], stories())['scenes'][1]
        self.assertEqual(row['kind'], 'illustration')
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            art, overlay = tmp / 'art.png', tmp / 'overlay.png'
            draw_scene(row, art, overlay, FONT)
            result = subprocess.run(
                ['ffmpeg', '-v', 'error', '-loop', '1', '-i', str(art), '-loop', '1', '-i', str(overlay),
                 '-filter_complex', video_filter(61), '-map', '[v]', '-frames:v', '61', str(tmp / 'f%03d.png')],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            first, last = Image.open(tmp / 'f001.png').convert('RGB'), Image.open(tmp / 'f061.png').convert('RGB')
            def changed(box):
                return ImageChops.difference(first.crop(box), last.crop(box)).getbbox() is not None
            self.assertTrue(changed((100, 190, 1130, 740)))        # the picture zooms and pans
            self.assertFalse(changed((1180, 230, 1780, 700)))      # caption and label: pixel-identical
            self.assertFalse(changed((0, 766, 1920, 1080)))        # narration text and source line


if __name__ == '__main__':
    unittest.main()
