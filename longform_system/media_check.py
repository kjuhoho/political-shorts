"""Check the actual encoded file, not predicted script timing."""
import json
import re
import subprocess


def check(video):
    probe = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)],
                           check=True, capture_output=True, text=True)
    data = json.loads(probe.stdout)
    visual = next(s for s in data['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in data['streams'] if s['codec_type'] == 'audio')
    duration = float(data['format']['duration'])
    if (visual['width'],visual['height']) != (1920,1080) or not 240 <= duration <= 360:
        raise RuntimeError('Video dimensions or duration invalid')
    if abs(float(visual['duration'])-float(audio['duration'])) > .5:
        raise RuntimeError('Audio/video duration mismatch')
    result = subprocess.run(['ffmpeg','-v','info','-i',str(video),'-af','volumedetect','-f','null','-'],
                            check=True, capture_output=True, text=True)
    volume = re.search(r'mean_volume:\s*(-?[\d.]+) dB', result.stderr)
    if not volume or float(volume.group(1)) < -40:
        raise RuntimeError('Silent or inaudible narration')
    return dict(passed=True,width=1920,height=1080,duration_s=duration,mean_volume_db=float(volume.group(1)))
