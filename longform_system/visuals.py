"""Deterministic editorial visuals. No LLM/image-generation calls or guessed photos.

Source cards are redrawn excerpts, not facsimiles of newspaper pages. Optional
media must be explicitly cleared in a local registry and tied to exact evidence.
"""
import hashlib
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1920, 1080
LICENSES = {'CC0', 'CC-BY-4.0', 'public-domain', 'own-work', 'KOGL-1'}


def text_block(draw, text, xy, font_path, width, height, size=46, color='#eff5fb'):
    # Display-only fonts omit common news Hanja and punctuation. Prefer the
    # OS's broad Korean font; never rewrite an actor's name to hide missing glyphs.
    for candidate in ('/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
                      'C:/Windows/Fonts/malgun.ttf'):
        if Path(candidate).exists():
            font_path = Path(candidate)
            break
    # Character wrapping handles Korean, URLs and tokens without spaces.
    for pts in range(size, 19, -2):
        font = ImageFont.truetype(str(font_path), pts)
        lines, line = [], ''
        for char in text:
            if char == '\n' or draw.textlength(line + char, font=font) > width:
                lines.append(line)
                line = '' if char == '\n' else char
            else:
                line += char
        if line:
            lines.append(line)
        if len(lines) * (pts + 12) <= height:
            for n, line in enumerate(lines):
                draw.text((xy[0], xy[1] + n*(pts+12)), line, font=font, fill=color)
            return
    raise ValueError('Visual text overflow; refusing to clip text')


def load_media(registry, stories):
    if not registry or not Path(registry).exists():
        return []
    registry = Path(registry)
    root = registry.parent.resolve()
    urls = {s['url'] for story in stories for s in story['sources']}
    media = json.loads(registry.read_text(encoding='utf-8'))
    for row in media:
        approved = row.get('reviewed') is True
        if row.get('approval') == 'automatic-metadata-v1':
            from .media_collect import approve
            source = next((s for story in stories for s in story['sources']
                           if s['url'] == row.get('source_url')),None)
            proof = row.get('proof',{})
            verdict, _ = approve(proof,source,[row.get('anchor','')]) if source else (None,None)
            if source and source.get('cross_check_status') == 'candidate_not_confirmation':
                from .international import event_review_valid
                primary = next(story['sources'][0] for story in stories if source in story['sources'])
                if not event_review_valid(source,primary):
                    verdict = None
            approved = bool(verdict and verdict['license'] == row.get('license')
                            and proof.get('source_url') == row.get('source_url')
                            and proof.get('credit') == row.get('credit')
                            and proof.get('license_url') == row.get('license_url')
                            and proof.get('asset_page') == row.get('asset_page'))
        if not approved or row.get('license') not in LICENSES:
            raise ValueError('Media rights have not been reviewed')
        for field in ('source_url', 'asset_page', 'credit', 'sha256', 'path', 'anchor', 'caption', 'license_url'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError('Missing media provenance: '+field)
        if row['source_url'] not in urls or len(row['anchor']) < 4:
            raise ValueError('Media does not match episode evidence')
        path = (root / row['path']).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in ('.jpg','.jpeg','.png','.mp4'):
            raise ValueError('Media path outside registry or unsupported format')
        if path.stat().st_size > 50_000_000:
            raise ValueError('Media exceeds 50 MB budget')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Media digest changed since editorial review')
        row['resolved_path'] = str(path)
    return media


def library_media(stories):
    """Small, visually inspected library; never fetch arbitrary search results."""
    root = Path(__file__).parent/'media'
    library = json.loads((root/'library.json').read_text(encoding='utf-8'))
    result = []
    for item in library:
        path = root/item['path']
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Library media escaped its directory')
        if item['license'] not in LICENSES or item['reviewed'] is not True:
            raise ValueError('Unreviewed library rights')
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Library image changed')
        for story in stories:
            source = story['sources'][0]
            evidence = source['title']+' '+source['body']
            if any(term in evidence for term in item['exclude']):
                continue
            for anchor in item['anchors']:
                if anchor in evidence:
                    result.append({**item,'source_url':source['url'],'anchor':anchor,
                                   'resolved_path':str(path.resolve())})
    return result


def plan(scenes, stories, registry=None):
    if len(stories) != 3:
        raise ValueError('Visual brief requires three evidence-backed issues')
    media = load_media(registry, stories)
    if registry and Path(registry).name == 'media-registry.json':
        media += load_media(Path(registry).with_name('auto-media-registry.json'),stories)
    media += library_media(stories)
    counts, result = {}, []
    headlines = [s['headline'] for s in stories]
    for index, (label, narration) in enumerate(scenes):
        counts[label] = counts.get(label, 0) + 1
        match = re.search(r'핵심\s*([123])', label)
        row = dict(index=index+1, label=label, narration=narration,
                   kind='overview', headlines=headlines, source=None, media=None,
                   disclosure='설명용 그래픽 · 실제 현장 영상 아님')
        if match:
            issue = int(match[1])-1
            source = stories[issue]['sources'][0]
            row.update(issue=issue+1, headline=headlines[issue], source={
                k: source[k] for k in ('name','url','published','title')})
            # Never isolate a number from its claim/denial/forecast context.
            numbers = re.findall(r'\d[\d,.]*(?:\s*(?:조|억|만))?\s*(?:명|원|%|건|석|개|년|월|일)', narration)
            row['kind'] = 'number' if numbers and counts[label] % 2 == 0 else 'issue'
            row['numbers'] = numbers[:2]
            # Short, literal excerpt. No generated quotes or page screenshots.
            excerpt = re.split(r'(?<=[.!?])\s+', source['body'].strip())[0]
            if counts[label] == 1 and len(excerpt) <= 160:
                row.update(kind='source', excerpt=excerpt,
                           disclosure='보도 원문 일부 · 출처를 바탕으로 재구성한 화면')
            issue_urls = {s['url'] for s in stories[issue]['sources']}
            candidates = [m for m in media if m['source_url'] in issue_urls and m['anchor'] in narration]
            if candidates and counts[label] > 1:
                row.update(kind='media', media=candidates[0], disclosure=candidates[0]['caption'])
                media_source = next(s for s in stories[issue]['sources'] if s['url']==candidates[0]['source_url'])
                row['source'] = {k:media_source[k] for k in ('name','url','published','title')}
        result.append(row)
    return dict(version=2, generated_without_llm=True, scenes=result,
                media_scenes=sum(r['kind']=='media' for r in result),
                notice='No unlicensed article images or guessed politician portraits are collected.')


def credits(scenes):
    used = {r['media']['sha256']:r['media'] for r in scenes if r.get('media')}
    if not used:
        return ''
    return '\n\n자료 화면 출처\n'+'\n\n'.join(
        f"{m['caption']}\n{m['credit']} / {m['license']}\n{m['asset_page']}\n"
        f"{m['license_url']}\n편집: 크기 조정·화면 배치·확대 이동. 권리자의 지지를 의미하지 않습니다."
        for m in used.values())


def draw_scene(row, background, overlay, font_path):
    image = Image.new('RGB', (W,H), '#091729')
    d = ImageDraw.Draw(image)
    for x in range(0,W,80):
        d.line((x,0,x,H), fill='#10243a', width=1)
    for y in range(0,H,80):
        d.line((0,y,W,y), fill='#10243a', width=1)
    d.rounded_rectangle((80,185,1840,740), radius=32, fill='#142e48')
    kind = row['kind']
    if kind == 'overview':
        for n, title in enumerate(row['headlines']):
            x = 120+n*575
            d.ellipse((x,225,x+90,315), fill='#48d4c0')
            text_block(d,str(n+1),(x+30,237),font_path,60,65,48,'#091729')
            text_block(d,title,(x,360),font_path,485,265,44)
        text_block(d,'오늘의 쟁점 3가지',(120,650),font_path,1500,65,36,'#48d4c0')
    elif kind == 'source':
        d.rounded_rectangle((140,215,1770,710), radius=18, fill='#e9f0f4')
        text_block(d,'SOURCE / 보도 원문',(185,250),font_path,1300,60,34,'#235b72')
        text_block(d,row['headline'],(185,325),font_path,1520,145,46,'#10243a')
        d.line((185,480,1720,480), fill='#9eb2c0',width=3)
        text_block(d,row['excerpt'],(185,505),font_path,1520,155,36,'#23394b')
    elif kind == 'number':
        text_block(d,'내레이션 속 수치 · 아래 문장과 함께 확인',(140,220),font_path,1600,70,34,'#48d4c0')
        text_block(d,' / '.join(row['numbers']),(140,320),font_path,1600,210,136)
        text_block(d,row['headline'],(140,555),font_path,1600,125,40)
    elif kind == 'media':
        asset = row['media']
        if Path(asset['resolved_path']).suffix.lower() != '.mp4':
            with Image.open(asset['resolved_path']) as src:
                fitted = ImageOps.contain(ImageOps.exif_transpose(src).convert('RGB'),(1680,510))
                image.paste(fitted,((W-fitted.width)//2,205+(510-fitted.height)//2))
    else:
        # Topic illustrations, not maps of military movements or invented data.
        topic = row['headline']+' '+row['narration']
        if any(t in topic for t in ('유엔','외교','회담','국제','미국','중국')):
            d.ellipse((180,265,590,675),outline='#48d4c0',width=9)
            for inset in (65,130):
                d.ellipse((180+inset,265,590-inset,675),outline='#5084a0',width=5)
            for y in (370,470,570):
                d.line((205,y,565,y),fill='#5084a0',width=5)
        elif any(t in topic for t in ('국회','법안','법원','재판')):
            d.polygon([(175,385),(385,245),(595,385)],fill='#48d4c0')
            for x in (215,310,405,500):
                d.rounded_rectangle((x,410,x+55,620),radius=8,fill='#5084a0')
            d.rectangle((175,645,595,665),fill='#48d4c0')
        else:
            d.rounded_rectangle((235,250,555,665),radius=24,fill='#e9f0f4')
            for y in range(315,590,65):
                d.line((280,y,500,y),fill='#23516a',width=12)
            d.ellipse((470,525,610,665),outline='#48d4c0',width=12)
            d.line((590,645,645,700),fill='#48d4c0',width=14)
        text_block(d,row['headline'],(735,290),font_path,1000,260,56)
        text_block(d,'사실과 쟁점을 구분해서 살펴봅니다',(735,625),font_path,1000,65,32,'#48d4c0')
    image.save(background)
    front = Image.new('RGBA',(W,H),(0,0,0,0))
    d = ImageDraw.Draw(front)
    d.rectangle((0,0,W,165),fill='#091729')
    text_block(d,'오늘의엔터  /  정치 브리핑',(85,38),font_path,1300,65,38)
    text_block(d,row['label'],(85,105),font_path,1650,50,30,'#48d4c0')
    d.rectangle((0,765,W,H),fill='#091729')
    text_block(d,row['narration'],(100,792),font_path,1720,165,48)
    source = row['source']
    disclosure = row['disclosure']
    if source:
        disclosure += f" | {source['name']} · 기사 발행 {source['published'][:10]}"
    if row['media']:
        disclosure += ' | '+row['media']['credit']+' / '+row['media']['license']
    text_block(d,disclosure,(100,985),font_path,1660,78,26,'#b9ccd8')
    text_block(d,f"{row['index']:02d}",(1790,1010),font_path,75,50,28,'#48d4c0')
    front.save(overlay)


def video_filter(frames, has_video=False):
    # Only artwork moves. Narration, dates and source attribution remain sharp.
    art = ('[0:v]scale=1680:510:force_original_aspect_ratio=decrease,'
           'pad=1920:1080:(ow-iw)/2:205+(510-ih)/2:color=0x091729,setsar=1,fps=30[art];'
           if has_video else
           f"[0:v]zoompan=z='1+0.018*on/{max(1,frames)}':"
           f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s=1920x1080:fps=30[art];")
    return art + ('[art][1:v]overlay=0:0:shortest=1,'
                  'drawbox=x=80:y=755:w=1760:h=4:color=0x48d4c0:t=fill,'
                  'format=yuv420p[v]')
