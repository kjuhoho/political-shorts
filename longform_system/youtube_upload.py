"""Independent uploader for the Today's Enter YouTube channel."""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any


def upload(video: Path, meta: dict[str, Any]) -> dict[str, str]:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    token = Path(os.environ["LONGFORM_YOUTUBE_TOKEN_FILE"])
    creds = Credentials.from_authorized_user_file(str(token), ["https://www.googleapis.com/auth/youtube.upload"])
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    if not creds.valid:
        raise RuntimeError("YouTube token is invalid or expired")
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    from .ledger import Ledger, episode_key, digest_file
    from .guards import accepted
    if not meta.get('quality') or not all(accepted(r) for r in meta['quality']):
        raise RuntimeError('Missing or failed 95-point reports')
    ledger = Ledger()
    key = episode_key(meta['date'])
    row, sha = ledger.reserve(key, digest_file(video))
    request = youtube.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": meta["title"][:100], "description": meta["description"][:4900],
                          "tags": meta["tags"], "categoryId": meta["category_id"],
                          "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
              "status": {"privacyStatus": meta["privacy_status"], "selfDeclaredMadeForKids": False}},
        media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True, mimetype="video/mp4"),
    )
    response = None
    while response is None:
        _, response = request.next_chunk(num_retries=0)
    video_id = response["id"]
    result = {"status": "uploaded", "video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}"}
    # Preserve the ID locally even if the durable completion write fails.
    video.with_suffix('.upload.json').write_text(json.dumps(result), encoding='utf-8')
    print(json.dumps(result), flush=True)
    ledger.put(key, {**row, **result}, sha)
    return result
