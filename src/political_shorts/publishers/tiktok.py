"""TikTok publisher via the Content Posting API (PULL_FROM_URL).

Requirements:
  * a TikTok developer app with the video.publish scope, approved for
    Direct Post (unaudited apps can only post as private / SELF_ONLY)
  * a user access token in TIKTOK_ACCESS_TOKEN
  * PUBLIC_MEDIA_BASE_URL serving ./output over https, and the domain must be
    verified in the TikTok developer portal (URL-prefix ownership)

Flow: POST /v2/post/publish/video/init/  (source=PULL_FROM_URL)
   -> poll POST /v2/post/publish/status/fetch/  until status == PUBLISH_COMPLETE
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from .base import Publisher, PublishResult

API = "https://open.tiktokapis.com/v2"


class TikTokPublisher(Publisher):
    name = "tiktok"

    def _is_configured(self) -> tuple[bool, str]:
        if not self.cfg.tiktok_enabled:
            return False, "TIKTOK_ENABLED=false"
        missing = [
            k
            for k, v in {
                "TIKTOK_ACCESS_TOKEN": self.cfg.tiktok_access_token,
                "PUBLIC_MEDIA_BASE_URL": self.cfg.public_media_base_url,
            }.items()
            if not v
        ]
        if missing:
            return False, "missing: " + ", ".join(missing)
        return True, ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.tiktok_access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _do_publish(self, video_path: Path, meta: dict[str, Any]) -> PublishResult:
        video_url = f"{self.cfg.public_media_base_url}/{video_path.name}"
        title = meta["title"][:150]

        init = requests.post(
            f"{API}/post/publish/video/init/",
            headers=self._headers(),
            json={
                "post_info": {
                    "title": title,
                    "privacy_level": "SELF_ONLY",  # safest default; widen after app audit
                    "disable_comment": False,
                    "disable_duet": True,
                    "disable_stitch": True,
                },
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "video_url": video_url,
                },
            },
            timeout=60,
        )
        init.raise_for_status()
        publish_id = init.json()["data"]["publish_id"]

        deadline = time.time() + 300
        status = "PROCESSING_UPLOAD"
        while time.time() < deadline:
            chk = requests.post(
                f"{API}/post/publish/status/fetch/",
                headers=self._headers(),
                json={"publish_id": publish_id},
                timeout=30,
            )
            chk.raise_for_status()
            status = chk.json()["data"].get("status", "PROCESSING_UPLOAD")
            if status in {"PUBLISH_COMPLETE", "FAILED"}:
                break
            time.sleep(5)

        ok = status == "PUBLISH_COMPLETE"
        return PublishResult(
            self.name, "ok" if ok else "error", False,
            remote_id=publish_id, detail=f"status={status} (privacy=SELF_ONLY)",
        )
