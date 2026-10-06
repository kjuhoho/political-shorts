"""Code-authored explanatory animation. No video-generation service or API."""
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

VERSION = 3
FPS = 30
SECONDS = 8
DISCLOSURE = '설명용 모션그래픽 · 실제 사건 현장 아님'
THEMES = {'supply-chain','diplomacy','legislation','economy','civic','evidence'}


def select_theme(narration, headline=''):
    from .illustrations import TOPICS
    scores = {name:sum(3*(term in narration)+(term in headline) for term in terms)
              for name,terms in TOPICS.items()}
    name = max(scores,key=scores.get) if any(scores.values()) else 'evidence'
    return 'supply-chain' if name == 'trade' else name


def background_frame(t,font,theme):
    """Slow topic-specific movement; no invented measurements, actors or events."""
    from .visuals import text_block
    im = Image.new('RGB',(1920,1080),'#091729')
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((80,185,1840,740),32,fill='#142e48')
    phase = 2*math.pi*(t%SECONDS)/SECONDS
    teal,ivory,muted = '#48c9b6','#e4d7b7','#587e92'
    titles = {'diplomacy':'외교와 국제 협력','legislation':'법안과 제도 논의',
              'economy':'경제와 생활','civic':'시민 생활과 공공 서비스','evidence':'자료와 쟁점 확인'}
    if theme == 'diplomacy':
        d.ellipse((370,255,800,685),outline=teal,width=8)
        for inset in (70,145):
            d.ellipse((370+inset,255,800-inset,685),outline=muted,width=4)
        for y in (365,470,575):
            d.line((395,y,775,y),fill=muted,width=4)
        for i in range(5):
            angle = phase+i*2*math.pi/5
            x,y = 585+300*math.cos(angle),470+185*math.sin(angle)
            d.ellipse((x-14,y-14,x+14,y+14),fill=ivory)
    elif theme in ('legislation','evidence'):
        # Abstract pages: marks are decoration, never simulated source quotes.
        for n in range(3):
            x,y = 280+n*145,275+20*math.sin(phase+n*.8)
            d.rounded_rectangle((x,y,x+285,y+360),16,fill=muted if n<2 else ivory)
        for y in range(340,600,48):
            d.line((625,y,800,y),fill='#355667',width=7)
        if theme == 'evidence':
            x,y = 745+55*math.cos(phase),500+45*math.sin(phase)
            d.ellipse((x-78,y-78,x+78,y+78),outline=teal,width=12)
            d.line((x+55,y+55,x+130,y+130),fill=teal,width=14)
        else:
            d.polygon([(175,400),(300,290),(425,400)],fill=teal)
            for x in (200,280,360):
                d.rectangle((x,425,x+30,625),fill=teal)
    elif theme == 'economy':
        d.polygon([(270,425),(460,270),(650,425)],fill=teal)
        d.rectangle((310,425,610,655),fill=ivory)
        d.rectangle((420,520,505,655),fill='#142e48')
        for n in range(3):
            x,y = 730+n*95,455+25*math.sin(phase+n)
            d.ellipse((x,y,x+75,y+75),fill='#d5b574',outline=ivory,width=4)
        d.line((220,680,1060,680),fill=muted,width=4)
    elif theme == 'civic':
        for x,h in ((230,210),(390,280),(570,175),(760,245)):
            d.rectangle((x,655-h,x+120,655),fill=muted)
            for y in range(685-h,620,55):
                d.rectangle((x+25,y,x+50,y+23),fill=ivory)
        bx = 420+150*math.sin(phase)
        d.rounded_rectangle((bx,575,bx+270,665),14,fill=teal)
        for x in (bx+30,bx+100,bx+170):
            d.rectangle((x,591,x+48,624),fill='#142e48')
        for x in (bx+45,bx+210):
            d.ellipse((x,645,x+35,680),fill=ivory)
    text_block(d,titles[theme],(1160,330),font,610,180,48)
    text_block(d,'설명을 돕는 개념 배경',(1160,610),font,610,70,30,'#b9ccd8')
    return im


def frame(t, font, theme='supply-chain'):
    if theme not in THEMES:
        raise ValueError('Unknown motion theme')
    if theme != 'supply-chain':
        return background_frame(t,font,theme)
    from .visuals import text_block
    im = Image.new('RGB', (1920,1080), '#091729')
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((80,185,1840,740),32,fill='#142e48')
    # A conceptual port, not a location, route map, data chart or event reenactment.
    d.rectangle((110,500,1170,710),fill='#1d5267')
    for y in range(525,700,35):
        for x in range(120,1070,125):
            shift = 16*math.sin(t*2*math.pi/SECONDS+y/80)
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


def render_art(output, font, ffmpeg='ffmpeg', theme='supply-chain'):
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    partial = output.with_suffix('.partial.mp4')
    cmd = [str(ffmpeg),'-y','-v','error','-f','rawvideo','-pix_fmt','rgb24',
           '-s','1920x1080','-r',str(FPS),'-i','pipe:0','-an','-c:v','libx264',
           '-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(partial)]
    proc = subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for i in range(FPS*SECONDS):
            proc.stdin.write(frame(i/FPS,font,theme).tobytes())
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


def picture_input(row, png, work, font, ffmpeg='ffmpeg', offset=0):
    if row.get('motion'):
        theme = row['motion']
        if theme not in THEMES:
            raise ValueError('Unknown motion theme')
        clip = Path(work)/f'{theme}-v{VERSION}.mp4'
        if not clip.exists():
            render_art(clip,font,ffmpeg,theme)
        return ['-stream_loop','-1','-ss',str(round(offset%SECONDS,6)),'-i',str(clip)],True,True
    asset = row.get('media')
    if asset and Path(asset['resolved_path']).suffix.lower()=='.mp4':
        return ['-stream_loop','-1','-i',asset['resolved_path']],True,False
    return ['-loop','1','-i',str(png)],False,False
