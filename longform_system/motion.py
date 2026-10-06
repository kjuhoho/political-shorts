"""Code-authored explanatory animation. No video-generation service or API."""
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

VERSION = 2
FPS = 30
SECONDS = 8
DISCLOSURE = '설명용 모션그래픽 · 실제 사건 현장 아님'


def frame(t, font):
    from .visuals import text_block
    im = Image.new('RGB', (1920,1080), '#091729')
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((80,185,1840,740),32,fill='#142e48')
    # A conceptual port, not a location, route map, data chart or event reenactment.
    d.rectangle((110,500,1170,710),fill='#1d5267')
    for y in range(525,700,35):
        for x in range(120,1070,125):
            shift = 16*math.sin(t*1.5+y/80)
            d.arc((x+shift,y,x+85+shift,y+12),0,170,fill='#428a9b',width=3)
    d.rectangle((900,485,1170,520),fill='#b7c3bb')
    d.rectangle((930,520,955,710),fill='#718f92')
    d.rectangle((1120,520,1145,710),fill='#718f92')
    # Smooth departure after loading. Last/first frames match for safe looping.
    phase = (t % SECONDS)/SECONDS
    departure = 95*(1-math.cos(max(0,phase-.5)*4*math.pi))/2 if phase>.5 else 0
    sx = -departure
    bob = 2*math.sin(t*math.pi/2)
    d.polygon([(200+sx,510+bob),(855+sx,510+bob),(800+sx,590+bob),
               (260+sx,590+bob)],fill='#e4d7b7')
    d.polygon([(230+sx,550+bob),(825+sx,550+bob),(800+sx,590+bob),
               (260+sx,590+bob)],fill='#da8963')
    d.rectangle((265+sx,410+bob,370+sx,510+bob),fill='#e8eddf')
    for x in (282,315,348):
        d.rectangle((x+sx,432+bob,x+17+sx,450+bob),fill='#326278')
    def container(x,y,color):
        d.rounded_rectangle((x,y,x+115,y+60),5,fill=color)
        for v in range(14,110,20):
            d.line((x+v,y+9,x+v,y+51),fill='#213c50',width=2)
    for x,color in ((405,'#6bb9af'),(525,'#d5b574'),(645,'#d98463')):
        container(x+sx,449+bob,color)
    # Gantry and trolley; container transfers dock -> ship, then reset reverses
    # the conceptual cycle. No claim about actual throughput or direction.
    d.rectangle((1090,250,1112,485),fill='#70c9ba')
    d.rectangle((620,250,1135,270),fill='#70c9ba')
    d.line((1100,275,1000,480),fill='#70c9ba',width=8)
    p = (1-math.cos(t*math.pi/4))/2
    cx = 985-340*p
    cy = 425-36*p-110*math.sin(math.pi*p)
    d.rectangle((cx+40,243,cx+75,278),fill='#e4d7b7')
    d.line((cx+58,278,cx+58,cy),fill='#dce7df',width=3)
    container(cx,cy,'#63bcae')
    text_block(d,'공급망은 연결입니다',(1230,235),font,535,130,46)
    for n,label in enumerate(('생산과 조달','운송과 유통','소비자에게 전달')):
        y = 390+n*96
        active = int(t/SECONDS*3)%3 == n
        color = '#48d4c0' if active else '#547487'
        d.ellipse((1235,y,1273,y+38),fill=color)
        text_block(d,label,(1300,y-5),font,460,65,34)
        if n<2:
            d.line((1254,y+45,1254,y+86),fill='#547487',width=3)
    text_block(d,'개념 설명 · 실제 운송 경로 아님',(1230,690),font,560,45,24,'#b9ccd8')
    return im


def render_art(output, font, ffmpeg='ffmpeg'):
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    partial = output.with_suffix('.partial.mp4')
    cmd = [str(ffmpeg),'-y','-v','error','-f','rawvideo','-pix_fmt','rgb24',
           '-s','1920x1080','-r',str(FPS),'-i','pipe:0','-an','-c:v','libx264',
           '-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(partial)]
    proc = subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for i in range(FPS*SECONDS):
            proc.stdin.write(frame(i/FPS,font).tobytes())
        proc.stdin.close()
        proc.wait(timeout=60)
        if proc.returncode:
            raise RuntimeError('Motion encode failed: '+proc.stderr.read().decode(errors='replace'))
        partial.replace(output)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stderr.close()
    return output


def picture_input(row, png, work, font, ffmpeg='ffmpeg'):
    if row.get('motion') == 'supply-chain':
        clip = Path(work)/f'supply-chain-v{VERSION}.mp4'
        if not clip.exists():
            render_art(clip,font,ffmpeg)
        return ['-stream_loop','-1','-i',str(clip)],True,True
    asset = row.get('media')
    if asset and Path(asset['resolved_path']).suffix.lower()=='.mp4':
        return ['-stream_loop','-1','-i',asset['resolved_path']],True,False
    return ['-loop','1','-i',str(png)],False,False
