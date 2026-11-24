"""Register new AI illustrations into the hash-checked library. Offline; no image generation here.

Workflow
  1. `--write-prompts`  writes PACK_PROMPTS.md (one image prompt per missing picture) for the image tool.
  2. Put <id>.png files next to manifest.json.
  3. `--check [--sheet out.png]`  validates them and draws one contact sheet to LOOK at.
  4. `--approve id id ...`  only after a person has looked at each picture: records its hash and prompt.

A file is never registered by default, because the library's rule is "inspected by a person".
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

from .illustrations import ROOT, build_prompt, spec

RATIO = 16 / 9
MIN_WIDTH = 1280
MAX_BYTES = 6_000_000
MIN_STDDEV = 12          # a flat or nearly blank picture is a failed generation, not artwork


def manifest(root=ROOT):
    return json.loads((Path(root) / 'manifest.json').read_text(encoding='utf-8'))


def pending(root=ROOT):
    done = {row['id'] for row in manifest(root)}
    return [name for name, row in spec().items() if name not in done and not row.get('core')]


def present(root=ROOT):
    return [name for name in pending(root) if (Path(root) / f'{name}.png').exists()]


def problems(path):
    path = Path(path)
    found = []
    if not path.exists():
        return ['missing file']
    if path.stat().st_size > MAX_BYTES:
        found.append(f'file is {path.stat().st_size // 1_000_000} MB; the repository budget is 6 MB each')
    try:
        with Image.open(path) as im:
            if im.format != 'PNG':
                found.append(f'not a PNG ({im.format})')
            width, height = im.size
            if width < MIN_WIDTH:
                found.append(f'too small: {width}x{height}; need at least {MIN_WIDTH} px wide')
            if abs(width / height - RATIO) > 0.04:
                found.append(f'not 16:9: {width}x{height}')
            if 'A' in im.getbands() and ImageStat.Stat(im.getchannel('A')).mean[0] < 250:
                found.append('has transparent areas')
            if max(ImageStat.Stat(im.convert('L')).stddev) < MIN_STDDEV:
                found.append('nearly blank picture')
    except Exception as exc:                      # unreadable or truncated file
        found.append(f'cannot be opened: {exc}')
    return found


def contact_sheet(root, names, output, columns=3, cell=(640, 360)):
    font = ImageFont.truetype(str(Path(__file__).resolve().parents[1] / 'assets/fonts/DoHyeon-Regular.ttf'), 26)
    rows = (len(names) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * (cell[0] + 12) + 12, rows * (cell[1] + 44) + 12), '#ffffff')
    draw = ImageDraw.Draw(sheet)
    for n, name in enumerate(names):
        x, y = 12 + (n % columns) * (cell[0] + 12), 12 + (n // columns) * (cell[1] + 44)
        with Image.open(Path(root) / f'{name}.png') as im:
            sheet.paste(im.convert('RGB').resize(cell), (x, y + 32))
        draw.text((x, y), f'{name}  ·  {spec()[name]["caption"]}', font=font, fill='#000000')
    sheet.save(output)
    return output


def register(root, names):
    root = Path(root)
    rows = manifest(root)
    known = {row['id'] for row in rows}
    added = []
    for name in names:
        row = spec().get(name)
        if row is None or row.get('core'):
            raise ValueError(f'{name} is not a pending picture in pack_spec.json')
        if name in known:
            raise ValueError(f'{name} is already registered')
        bad = problems(root / f'{name}.png')
        if bad:
            raise ValueError(f'{name}: ' + '; '.join(bad))
        added.append(dict(id=name, path=f'{name}.png',
                          sha256=hashlib.sha256((root / f'{name}.png').read_bytes()).hexdigest(),
                          type='ai-explanatory', visually_checked=True, generation_mode='built-in image_gen',
                          prompt=build_prompt(row['subject'])))
    target = root / 'manifest.json'
    partial = target.with_suffix('.json.partial')
    partial.write_text(json.dumps(rows + added, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(partial, target)
    return added


def prompts_markdown(root=ROOT):
    lines = ['# 설명 일러스트 추가 제작 프롬프트', '',
             '이미지 도구(built-in image_gen)로 아래 그림을 한 장씩 만들고 이 폴더에 `<id>.png`로 저장합니다.', '',
             '- 가로형 16:9, 폭 1280px 이상, PNG, 장당 6MB 이하. 기존 6장과 같은 구도·색감(남색·청록·아이보리 종이 공예)을 유지합니다.',
             '- 사람, 깃발, 로고, 글자, 숫자, 차트가 그림에 있으면 안 됩니다. 실제 장소·사건처럼 보여서도 안 됩니다.',
             '- 저장 후에는 아무것도 등록하지 말고 그대로 알려주세요. 사람이 눈으로 확인한 뒤 등록합니다.', '']
    for n, name in enumerate(pending(root), 1):
        row = spec()[name]
        lines += [f'## {n}. {name} — {row["caption"]}', f'저장 파일명: `{name}.png`', '', '```', build_prompt(row['subject']), '```', '']
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--write-prompts', type=Path)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--sheet', type=Path)
    ap.add_argument('--approve', nargs='+', metavar='ID')
    args = ap.parse_args(argv)
    if args.write_prompts:
        args.write_prompts.write_text(prompts_markdown(args.root), encoding='utf-8')
        print(f'{len(pending(args.root))} prompts -> {args.write_prompts}')
    if args.check or args.sheet:
        names = present(args.root)
        print(f'pending {len(pending(args.root))}, files present {len(names)}')
        for name in pending(args.root):
            if name not in names:
                print(f'  waiting  {name}')
        for name in names:
            bad = problems(args.root / f'{name}.png')
            print(f'  {"FAIL" if bad else "ok  "}  {name}' + (f'  {"; ".join(bad)}' if bad else ''))
        if args.sheet and names:
            print('contact sheet ->', contact_sheet(args.root, names, args.sheet))
    if args.approve:
        for row in register(args.root, args.approve):
            print('registered', row['id'], row['sha256'][:12])
    return 0


if __name__ == '__main__':
    sys.exit(main())
