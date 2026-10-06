"""Offline, hash-checked AI illustration library, never a source of facts."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).parent / 'illustrations'
TOPICS = {
    'diplomacy': ('외교','회담','유엔','국제','정상','동맹'),
    'legislation': ('국회','법안','입법','의회','표결'),
    'economy': ('예산','경제','물가','세금','재정','금리','주택'),
    'trade': ('수출','수입','무역','관세','공급망','항만'),
    'civic': ('시민','생활','복지','교통','지역','주민'),
    'evidence': ('자료','확인','발표','조사','문서','근거'),
}


@lru_cache(maxsize=1)
def library():
    rows = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    result = {}
    for row in rows:
        path = (ROOT / row['path']).resolve()
        if not path.is_relative_to(ROOT.resolve()) or path.suffix != '.png':
            raise ValueError('Invalid illustration path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Illustration changed since visual inspection')
        if row.get('type') != 'ai-explanatory' or row.get('visually_checked') is not True:
            raise ValueError('Uninspected illustration')
        result[row['id']] = {**row, 'resolved_path':str(path)}
    if set(result) != set(TOPICS):
        raise ValueError('Incomplete illustration library')
    return result


def choose(narration, headline='', index=0):
    # Prefer the sentence over the overall headline. Unknown topics get neutral
    # research artwork, never an unrelated event or a fabricated actor portrait.
    scores = {name:sum(3*(term in narration)+(term in headline) for term in terms)
              for name,terms in TOPICS.items()}
    best = max(scores.values())
    candidates = [name for name,score in scores.items() if score == best]
    name = candidates[index % len(candidates)] if best else 'evidence'
    return library()[name]
