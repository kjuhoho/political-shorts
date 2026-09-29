"""Opt-in Fish integration; free model only, no automatic retries/fallbacks."""
import hashlib
import json
import os
from pathlib import Path
import requests

MODEL = 's2.1-pro-free'


def provider():
    value = os.environ.get('LONGFORM_TTS_PROVIDER', '').strip() or 'edge'
    if value not in ('edge', 'fish'):
        raise RuntimeError('Unknown longform TTS provider')
    return value


def validate_config():
    if provider() != 'fish':
        return
    if os.environ.get('LONGFORM_FISH_USE_ACK') != 'true':
        raise RuntimeError('Fish use requires acknowledgement of voice rights, data retention and applicable usage terms')
    if not os.environ.get('LONGFORM_FISH_API_KEY', '').strip():
        raise RuntimeError('Missing LONGFORM_FISH_API_KEY')
    if not os.environ.get('LONGFORM_FISH_VOICE_ID', '').strip():
        raise RuntimeError('Missing LONGFORM_FISH_VOICE_ID')


def synthesize(text, output):
    validate_config()
    if provider() != 'fish':
        raise RuntimeError('Fish is not selected')
    if not text.strip() or len(text) > 500:
        raise RuntimeError('Fish requires a nonempty, bounded narration segment')
    output = Path(output)
    payload = dict(text=text, reference_id=os.environ['LONGFORM_FISH_VOICE_ID'].strip(),
                   format='mp3', mp3_bitrate=128, prosody={'speed':0.95})
    key = hashlib.sha256(json.dumps([MODEL, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    receipt = output.with_suffix('.fish.json')
    if receipt.exists():
        old = json.loads(receipt.read_text(encoding='utf-8'))
        if old.get('key') == key:
            if old.get('status') == 'complete' and output.exists():
                if hashlib.sha256(output.read_bytes()).hexdigest() == old.get('sha256'):
                    return
            raise RuntimeError('Fish request outcome unknown or cached audio damaged; no blind retry')
    output.parent.mkdir(parents=True, exist_ok=True)
    state = dict(key=key, status='requested', model=MODEL, utf8_bytes=len(text.encode()))
    receipt.write_text(json.dumps(state), encoding='utf-8')
    try:
        response = requests.post('https://api.fish.audio/v1/tts',
            headers={'Authorization':'Bearer '+os.environ['LONGFORM_FISH_API_KEY'].strip(), 'model':MODEL},
            json=payload, timeout=(10,120), allow_redirects=False)
    except requests.RequestException:
        raise RuntimeError('Fish network failure; no automatic retry or paid fallback') from None
    # Never log response bodies: a provider error may echo account/request data.
    if response.status_code != 200:
        raise RuntimeError(f'Fish HTTP {response.status_code}; no retry or paid fallback')
    audio = response.content
    if len(audio) < 128 or not (audio.startswith(b'ID3') or (audio[0] == 255 and audio[1] & 224 == 224)):
        raise RuntimeError('Fish did not return recognizable MP3 audio')
    temp = output.with_suffix('.partial')
    temp.write_bytes(audio)
    temp.replace(output)
    state.update(status='complete', sha256=hashlib.sha256(audio).hexdigest())
    receipt.write_text(json.dumps(state), encoding='utf-8')
