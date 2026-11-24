"""Offline, hash-checked AI illustration library, never a source of facts."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).parent / 'illustrations'
# The six code-motion themes (motion.py) are keyed by these names. Do NOT grow this
# dict for new pictures: new pictures live in pack_spec.json.
TOPICS = {
    'diplomacy': ('외교','회담','유엔','국제','정상','동맹'),
    'legislation': ('국회','법안','입법','의회','표결'),
    'economy': ('예산','경제','물가','세금','재정','금리','주택'),
    'trade': ('수출','수입','무역','관세','공급망','항만'),
    'civic': ('시민','생활','복지','교통','지역','주민'),
    'evidence': ('자료','확인','발표','조사','문서','근거'),
}
HOLD = 3          # one picture stays for at most this many consecutive sentences (~25 s)
FALLBACK = 'evidence'
PROMPT_TEMPLATE = (
    'Use case: illustration-story. Asset type: reusable explanatory background for a neutral Korean news '
    'briefing, not a news photograph. Create one polished wide 16:9 editorial illustration: {subject}. '
    'Navy, teal, warm ivory palette, refined dimensional paper-cut style with subtle grain and soft lighting. '
    'Rich but uncluttered composition, central subject with generous safe margins. '
    'No people, no flags, no logos, no text, no numbers, no borders, no charts or invented factual data. '
    'Clearly illustrative, not photorealistic and not depicting an actual event.')


def build_prompt(subject):
    return PROMPT_TEMPLATE.format(subject=subject.strip().rstrip('.'))


@lru_cache(maxsize=1)
def spec():
    """Every picture the pack is meant to contain, with or without artwork yet."""
    rows = json.loads((ROOT / 'pack_spec.json').read_text(encoding='utf-8'))['pictures']
    result = {}
    for row in rows:
        if row['id'] in result:
            raise ValueError('Duplicate illustration id: ' + row['id'])
        if not row.get('caption') or not row.get('keywords'):
            raise ValueError('Illustration needs a caption and keywords: ' + row['id'])
        if not row.get('core') and not row.get('subject'):
            raise ValueError('New illustration needs a subject prompt: ' + row['id'])
        result[row['id']] = row
    if set(TOPICS) - set(result):
        raise ValueError('Core illustrations missing from pack_spec.json')
    return result


@lru_cache(maxsize=1)
def library(root=None):
    """Only pictures that exist, match their recorded hash and were looked at by a person."""
    base = Path(root) if root else ROOT
    wanted = spec()
    rows = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
    result = {}
    for row in rows:
        path = (base / row['path']).resolve()
        if not path.is_relative_to(base.resolve()) or path.suffix != '.png':
            raise ValueError('Invalid illustration path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Illustration changed since visual inspection')
        if row.get('type') != 'ai-explanatory' or row.get('visually_checked') is not True:
            raise ValueError('Uninspected illustration')
        if row['id'] not in wanted:
            raise ValueError('Illustration is not in pack_spec.json: ' + row['id'])
        entry = wanted[row['id']]
        result[row['id']] = {**row, 'caption': entry['caption'], 'keywords': list(entry['keywords']),
                             'neutral': bool(entry.get('neutral')), 'resolved_path': str(path)}
    if not set(TOPICS) <= set(result):
        raise ValueError('Incomplete illustration library')
    return result


def score(entry, narrations, headline=''):
    return (sum(3 * (term in text) for text in narrations for term in entry['keywords'])
            + sum(term in headline for term in entry['keywords']))


def pick(narrations, headline='', used=None, last=None, seed=0):
    """Choose one picture for a short run of sentences.

    Topic evidence decides first. Repeating the previous picture or one already used in this
    video costs points, so a larger pack rotates instead of looping. Sentences that match no
    topic get a neutral picture, never an unrelated event.
    """
    lib, used = library(), used or {}
    names = list(lib)
    raw = {name: score(lib[name], narrations, headline) for name in names}
    topical = [name for name in names if raw[name] > 0]
    pool = topical or [name for name in names if lib[name]['neutral']] or [FALLBACK]
    def value(name):
        penalty = 4 * (name == last) + 2 * used.get(name, 0)
        return (raw[name] - penalty, -((names.index(name) + seed) % len(names)))
    return lib[max(pool, key=value)]


def choose(narration, headline='', index=0):
    """Single-sentence choice, kept for callers that do not plan runs."""
    return pick([narration], headline, seed=index)
