"""Standalone 16:9 renderer for Today's Enter longform briefings.

It intentionally does not import the shorts package.  Input is a reviewed
Markdown script; output is an MP4 plus a render manifest for the independent
longform uploader.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import edge_tts

W, H, FPS = 1920, 1080, 30
PALETTES = [(12, 28, 48), (20, 48, 71), (43, 37, 70), (23, 59, 58), (62, 42, 32)]


def clean_script(path: Path) -> list[tuple[str, str]]:
    """Return section-labelled narration chunks, excluding production notes."""
    raw = path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", raw)
    chunks: list[tuple[str, str]] = []
    for section in sections:
        lines = [line.strip() for line in section.splitlines()
                 if line.strip() and not line.startswith(">") and not line.startswith("[화면 출처")]
        if not lines:
            continue
        label = re.sub(r"^\d+:\d+[–-]\d+:\d+\s*\|\s*", "", lines[0])
        body = " ".join(lines[1:])
        body = re.sub(r"\[SHORTS_HOOK\]\s*", "", body)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        for i in range(0, len(sentences)):
            text = sentences[i]
            if text:
                # Preserve every word while limiting one readable screen.
                piece = ''
                for word in text.split():
                    candidate = (piece + ' ' + word).strip()
                    if len(candidate) > 110 and piece:
                        chunks.append((label, piece))
                        piece = word
                    else:
                        piece = candidate
                if piece:
                    chunks.append((label, piece))
    return chunks


async def make_audio(text: str, output: Path, voice: str) -> None:
    await edge_tts.Communicate(text, voice=voice, rate="-5%").save(str(output))


def duration(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       check=True, capture_output=True, text=True)
    return float(p.stdout.strip())


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def card(label: str, text: str, output: Path, index: int, font_path: Path, source: str = '') -> None:
    base = PALETTES[index % len(PALETTES)]
    image = Image.new("RGB", (W, H), base)
    draw = ImageDraw.Draw(image)
    # Simple editorial grid, deliberately restrained for a neutral briefing.
    for x in range(-H, W, 190):
        draw.line((x, 0, x + H, H), fill=tuple(min(255, c + 10) for c in base), width=3)
    title_font = ImageFont.truetype(str(font_path), 43)
    body_font = ImageFont.truetype(str(font_path), 58)
    small_font = ImageFont.truetype(str(font_path), 27)
    draw.rounded_rectangle((110, 92, 710, 160), radius=18, fill=(236, 242, 248))
    draw.text((142, 108), "오늘의엔터 | 정치 5분 브리핑", font=small_font, fill=(18, 39, 58))
    draw.text((112, 230), label[:34], font=title_font, fill=(129, 211, 236))
    lines = wrap(draw, text, body_font, 1600)
    if len(lines) > 5:
        raise ValueError('Subtitle does not fit; refusing to silently truncate it')
    y = 340
    for line in lines:
        draw.text((112, y), line, font=body_font, fill=(250, 252, 255), stroke_width=1, stroke_fill=(0, 0, 0))
        y += 94
    draw.line((112, 895, 1808, 895), fill=(129, 211, 236), width=3)
    disclosure = source or "원문 출처와 발행일은 영상 설명란에서 확인할 수 있습니다."
    if draw.textbbox((0,0), disclosure, font=small_font)[2] > 1600:
        raise ValueError('Source disclosure too wide')
    draw.text((112, 928), disclosure, font=small_font, fill=(220, 230, 238))
    draw.text((1770, 928), f"{index + 1:02d}", font=small_font, fill=(220, 230, 238))
    image.save(output, "PNG")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def render(script_path: Path, output: Path, font_path: Path, voice: str) -> dict:
    work = output.parent / f".{output.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    scenes = clean_script(script_path)
    if len(scenes) < 5:
        raise ValueError("A longform script needs at least five narration chunks")
    # Measure natural narration before spending time encoding every scene.
    # Keep this diagnostic audio so a length hold is concrete and reviewable.
    timing_audio = output.with_suffix('.timing.mp3')
    asyncio.run(make_audio(' '.join(text for _,text in scenes), timing_audio, voice))
    spoken_seconds = duration(timing_audio)
    output.with_suffix('.timing.json').write_text(json.dumps({'natural_duration_s':spoken_seconds}), encoding='utf-8')
    if not 240 <= spoken_seconds <= 360:
        raise ValueError(f'Natural narration lasts {spoken_seconds:.1f}s; 5-minute length policy not met')
    clips: list[Path] = []
    manifest_scenes = []
    raw = script_path.read_text(encoding='utf-8')
    sources = re.findall(r'\[화면 출처 텍스트: (.*?)\]', raw)
    seen_labels = set()
    qa = output.parent / 'qa'
    qa.mkdir(exist_ok=True)
    for i, (label, text) in enumerate(scenes):
        png, mp3, clip = work / f"{i:02d}.png", work / f"{i:02d}.mp3", work / f"{i:02d}.mp4"
        match = re.search(r'핵심\s*(\d)', label)
        source = ''
        if match:
            source = sources[int(match.group(1))-1]
            source = '출처: ' + ' | '.join(source.split(' | ')[:2]) + ' | 원문은 설명란'
        card(label, text, png, i, font_path, source)
        if label not in seen_labels:
            Image.open(png).save(qa / f'{i:02d}.png')
            seen_labels.add(label)
        asyncio.run(make_audio(text, mp3, voice))
        seconds = duration(mp3)
        frames = max(1, round(seconds * FPS))
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(png), "-i", str(mp3),
             "-filter_complex", f"[0:v]fps={FPS},format=yuv420p[v]",
             "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-t", str(seconds), str(clip)])
        clips.append(clip)
        manifest_scenes.append({"index": i + 1, "label": label, "text": text, "duration_s": seconds})
    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{clip.as_posix()}'\n" for clip in clips), encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(output)])
    actual = duration(output)
    manifest = {"video": str(output), "duration_s": actual,
                "scenes": manifest_scenes, "source_script": str(script_path)}
    output.with_suffix(".render.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font", type=Path, default=Path("assets/fonts/DoHyeon-Regular.ttf"))
    parser.add_argument("--voice", default="ko-KR-SunHiNeural")
    args = parser.parse_args()
    print(json.dumps(render(args.script, args.output, args.font, args.voice), ensure_ascii=False, indent=2))
