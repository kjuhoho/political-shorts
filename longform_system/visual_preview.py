"""Offline visual smoke render using previously rendered narration; no API calls.

Not publishable: this intentionally reuses historical news as a design preview.
"""
import argparse
import json
import subprocess
from pathlib import Path

from .visuals import plan, draw_scene, video_filter
from .motion import picture_input


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--ffmpeg',required=True)
    ap.add_argument('--all-scenes',action='store_true')
    ap.add_argument('--font',type=Path,default=Path('assets/fonts/DoHyeon-Regular.ttf'))
    args = ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    manifest_path = next(args.bundle.glob('*.render.json'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    scenes = [(s['label'],s['text']) for s in manifest['scenes']]
    stories = json.loads((args.bundle/'sources.json').read_text(encoding='utf-8'))
    board = plan(scenes,stories)
    video = manifest_path.with_suffix('').with_suffix('.mp4')
    # Four representative kinds; narration and duration come from the old MP4.
    offsets, offset = [], 0
    for row in manifest['scenes']:
        offsets.append(offset)
        offset += row['duration_s']
    selected, kinds = [], set()
    for i,row in enumerate(board['scenes']):
        key = row.get('motion') or row['kind']
        if args.all_scenes or key not in kinds:
            selected.append(i)
            kinds.add(key)
    clips = []
    for i in selected:
        row = board['scenes'][i]
        row['label'] = '디자인 검토용 · 과거 보도 재사용 · '+row['label']
        png, overlay, clip = [args.output/f'{i:02d}{suffix}' for suffix in ('.png','.overlay.png','.mp4')]
        draw_scene(row,png,overlay,args.font)
        seconds = manifest['scenes'][i]['duration_s']
        inputs, has_video, full_frame = picture_input(row,png,args.output,args.font,args.ffmpeg,offsets[i])
        subprocess.run([args.ffmpeg,'-y','-v','error',*inputs,
            '-loop','1','-i',str(overlay),'-ss',str(offsets[i]),'-i',str(video),
            '-filter_complex',video_filter(round(seconds*30),has_video,full_frame),
            '-map','[v]','-map','2:a','-t',str(seconds),'-c:v','libx264','-preset','veryfast',
            '-crf','22','-c:a','aac',str(clip)],check=True)
        subprocess.run([args.ffmpeg,'-y','-v','error','-ss',str(seconds/2),'-i',str(clip),
            '-frames:v','1',str(args.output/f'{i:02d}.encoded.png')],check=True)
        clips.append(clip)
    concat = args.output/'concat.txt'
    concat.write_text(''.join(f"file '{p.resolve().as_posix()}'\n" for p in clips),encoding='utf-8')
    subprocess.run([args.ffmpeg,'-y','-v','error','-f','concat','-safe','0','-i',str(concat),
        '-c','copy','-movflags','+faststart',str(args.output/'preview.mp4')],check=True)
    (args.output/'visual-plan.json').write_text(json.dumps(board,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(selected_scenes=selected,kinds=sorted(kinds),api_calls=0)))


if __name__ == '__main__':
    main()
