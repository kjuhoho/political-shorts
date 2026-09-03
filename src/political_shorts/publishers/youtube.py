"""YouTube Data API v3 uploader (OAuth installed-app flow).

Setup (one time):
  1. Google Cloud Console -> enable "YouTube Data API v3".
  2. Create an OAuth client ID of type "Desktop app".
  3. Download the JSON to the path in YOUTUBE_CLIENT_SECRET_FILE.
  4. First real run opens a browser to authorise; the token is cached in
     YOUTUBE_TOKEN_FILE.

Quota note: an upload costs ~1600 units of the default 10,000/day.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings
from .base import Publisher, PublishResult

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubePublisher(Publisher):
    name = "youtube"

    def _is_configured(self) -> tuple[bool, str]:
        secret = Path(self.cfg.youtube_client_secret_file or "")
        if not self.cfg.youtube_client_secret_file:
            return False, "YOUTUBE_CLIENT_SECRET_FILE not set"
        if not secret.exists():
            return False, f"client secret file not found: {secret}"
        return True, ""

    # -- oauth ---------------------------------------------------------------
    def _interactive(self) -> bool:
        """Only ever open a browser consent flow when a human is at the terminal.
        A scheduled (pythonw / no-TTY) run must fail loudly instead of hanging
        forever on `run_local_server()`."""
        import sys

        if getattr(self.cfg, "youtube_allow_interactive_auth", False):
            return True
        try:
            return bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            return False

    def _credentials(self):
        import time

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = Path(self.cfg.youtube_token_file or "secrets/token_youtube.json")
        token_path.parent.mkdir(parents=True, exist_ok=True)
        creds = None
        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            except Exception as exc:  # corrupt / scope mismatch -> re-auth
                self.log.warning("stored token unusable (%s); re-authorising", exc)
                creds = None

        if creds and creds.valid:
            return creds

        if creds and creds.refresh_token:
            # Refresh the access token, retrying transient network failures — the
            # scheduled run often fires before the connection is fully up.
            delay = 15.0
            for attempt in range(1, 5):
                try:
                    creds.refresh(Request())
                    token_path.write_text(creds.to_json(), encoding="utf-8")
                    return creds
                except Exception as exc:
                    msg = f"{type(exc).__name__}: {exc}".lower()
                    transient = any(k in msg for k in ("timed out", "timeout", "connection", "unreachable", "reset", "503", "502"))
                    if transient and attempt < 4:
                        self.log.warning("token refresh %d/4 failed (%s) — retrying in %.0fs",
                                         attempt, type(exc).__name__, delay)
                        time.sleep(delay)
                        delay = min(delay * 2, 120)
                        continue
                    raise RuntimeError(
                        f"YouTube token refresh failed ({type(exc).__name__}: {exc}). "
                        "If this persists, run `python -m political_shorts auth youtube` "
                        "to re-authorise."
                    ) from exc

        if not self._interactive():
            raise RuntimeError(
                "YouTube credentials are missing/expired and no interactive terminal "
                "is available to authorise. Run `python -m political_shorts auth youtube` "
                "once from a console, then the scheduled task will work."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            self.cfg.youtube_client_secret_file, SCOPES
        )
        # prompt=consent + offline guarantees a refresh_token every run.
        creds = flow.run_local_server(
            port=0, access_type="offline", prompt="consent",
            authorization_prompt_message=(
                "브라우저에서 Google 로그인/동의를 진행하세요. "
                "자동으로 안 열리면 이 URL을 여세요:\n{url}"
            ),
            success_message="인증 완료. 이 창을 닫고 터미널로 돌아가세요.",
        )
        if not creds.refresh_token:
            self.log.warning(
                "no refresh_token returned — unattended runs will fail later. "
                "Delete the token file and re-run `auth youtube`."
            )
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    # -- upload ------------------------------------------------------------
    def _do_publish(self, video_path: Path, meta: dict[str, Any]) -> PublishResult:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = self._credentials()
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

        status = {
            "privacyStatus": meta.get("privacy_status", "private"),
            "selfDeclaredMadeForKids": bool(meta.get("made_for_kids", False)),
        }
        # Server-side scheduled publish: upload now as private, YouTube flips it
        # public at `publish_at` (RFC3339) on its own — no dependency on this PC
        # being on / online at that moment.
        publish_at = meta.get("publish_at")
        if publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at

        body = {
            "snippet": {
                "title": meta["title"][:100],
                "description": meta["description"][:4900],
                "tags": meta.get("tags", [])[:400],
                "categoryId": str(meta.get("category_id", "25")),
                "defaultLanguage": "ko",
                "defaultAudioLanguage": "ko",
            },
            "status": status,
        }
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        response = None
        while response is None:
            _status, response = request.next_chunk()

        vid = response["id"]
        url = f"https://youtube.com/shorts/{vid}"
        detail = (f"scheduled publishAt={publish_at}" if publish_at
                  else f"privacy={status['privacyStatus']}")
        return PublishResult(self.name, "ok", False, remote_id=vid, url=url, detail=detail)
