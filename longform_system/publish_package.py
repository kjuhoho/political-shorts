"""Publish precisely the inspected artifact; do not regenerate the episode."""
import argparse
import json
from pathlib import Path
from .guards import accepted, validate
from .research import now
from .ledger import digest_file
from .youtube_upload import upload
from .media_check import check as check_media


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    packages = list(args.directory.glob('*.meta.json'))
    if len(packages) != 1:
        raise RuntimeError('Expected exactly one reviewed episode')
    meta_path = packages[0]
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    stem = meta_path.name.removesuffix('.meta.json')
    video = args.directory / (stem + '.mp4')
    script = (args.directory / (stem + '.script.md')).read_text(encoding='utf-8')
    if meta['date'] != now().date().isoformat():
        raise RuntimeError('Episode is no longer current; review the date before posting')
    validate(script, meta['sources'])
    if not meta.get('video_sha256') or digest_file(video) != meta['video_sha256']:
        raise RuntimeError('Video integrity mismatch')
    if not accepted(meta.get('title_review', {})):
        raise RuntimeError('Title review unavailable')
    # Recheck the downloaded MP4 immediately before insertion, including
    # artifacts built before media-check manifests were added.
    meta['media_check'] = check_media(video)
    result = upload(video, meta)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
