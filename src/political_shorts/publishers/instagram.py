"""Instagram Reels publisher via the Instagram Graph API.

Requirements the API imposes (not negotiable):
  * an Instagram *Business* or *Creator* account linked to a Facebook Page
  * a long-lived access token with instagram_content_publish permission
  * the video must be reachable at a PUBLIC https URL — the API pulls it, it
    does not accept a direct file upload. Set PUBLIC_MEDIA_BASE_URL to a host
    that serves ./output (e.g. a static bucket, a tunnel, or your own server).

Flow: POST /{ig-user-id}/media (media_type=REELS, video_url, caption)
   -> poll GET /{container-id}?fields=status_code until FINISHED
   -> POST /{ig-user-id}/media_publish (creation_id=container-id)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from .base import Publisher, PublishResult

GRAPH = "https://graph.facebook.com/v21.0"


class InstagramPublisher(Publisher):
    name = "instagram"

    def _is_configured(self) -> tuple[bool, str]:
        if not self.cfg.instagram_enabled:
            return False, "INSTAGRAM_ENABLED=false"
        missing = [
            k
            for k, v in {
                "INSTAGRAM_USER_ID": self.cfg.instagram_user_id,
                "INSTAGRAM_ACCESS_TOKEN": self.cfg.instagram_access_token,
                "PUBLIC_MEDIA_BASE_URL": self.cfg.public_media_base_url,
            }.items()
            if not v
        ]
        if missing:
            return False, "missing: " + ", ".join(missing)
        return True, ""

    def _public_url(self, video_path: Path) -> str:
        return f"{self.cfg.public_media_base_url}/{video_path.name}"

    def _do_publish(self, video_path: Path, meta: dict[str, Any]) -> PublishResult:
        token = self.cfg.instagram_access_token
        ig_user = self.cfg.instagram_user_id
        video_url = self._public_url(video_path)
        caption = (meta["title"] + "\n\n" + meta["description"])[:2100]

        create = requests.post(
            f"{GRAPH}/{ig_user}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": token,
            },
            timeout=60,
        )
        create.raise_for_status()
        container_id = create.json()["id"]

        # poll for processing to finish (max ~5 min)
        deadline = time.time() + 300
        status = "IN_PROGRESS"
        while time.time() < deadline:
            chk = requests.get(
                f"{GRAPH}/{container_id}",
                params={"fields": "status_code", "access_token": token},
                timeout=30,
            )
            chk.raise_for_status()
            status = chk.json().get("status_code", "IN_PROGRESS")
            if status in {"FINISHED", "ERROR", "EXPIRED"}:
                break
            time.sleep(5)
        if status != "FINISHED":
            return PublishResult(
                self.name, "error", False, remote_id=container_id,
                detail=f"container status={status}",
            )

        pub = requests.post(
            f"{GRAPH}/{ig_user}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=60,
        )
        pub.raise_for_status()
        media_id = pub.json()["id"]
        return PublishResult(
            self.name, "ok", False, remote_id=media_id,
            detail=f"pulled from {video_url}",
        )
