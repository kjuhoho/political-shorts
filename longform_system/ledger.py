"""Pre-upload reservation. Unknown outcomes never trigger another insert."""
import base64
import hashlib
import json
import os
import requests


def episode_key(day):
    return f'longform-{day}'


class Ledger:
    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        self.headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'}

    def url(self, key):
        return f'https://api.github.com/repos/{self.repo}/contents/longform_system/state/uploads/{key}.json'

    def get(self, key):
        r = requests.get(self.url(key), headers=self.headers, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        row = r.json()
        return json.loads(base64.b64decode(row['content'])), row['sha']

    def put(self, key, data, sha=None):
        payload = {'message': f'longform: {data["status"]} {key} [skip ci]',
                   'content': base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()}
        if sha:
            payload['sha'] = sha
        r = requests.put(self.url(key), headers=self.headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()['content']['sha']

    def reserve(self, key, digest):
        if self.get(key):
            raise RuntimeError('Episode already reserved/published; reconcile before any new upload')
        row = {'status': 'reserved', 'episode': key, 'sha256': digest, 'run_id': os.environ.get('GITHUB_RUN_ID')}
        return row, self.put(key, row)


def digest_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()
