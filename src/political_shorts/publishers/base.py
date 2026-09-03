from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, settings
from ..logging_setup import get_logger

# transient network failures worth retrying (connect timeouts, DNS blips, 5xx)
_RETRYABLE = ("timed out", "timeout", "connection", "temporarily", "unreachable",
              "reset by peer", "handshake", "503", "502", "500", "429")


@dataclass
class PublishResult:
    platform: str
    status: str            # ok | error | dry-run | disabled
    dry_run: bool
    remote_id: str = ""
    url: str = ""
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Publisher:
    name = "base"

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings
        self.log = get_logger(f"publish.{self.name}")

    # -- to be implemented by subclasses -------------------------------------
    def _is_configured(self) -> tuple[bool, str]:
        """Return (ok, reason-if-not)."""
        raise NotImplementedError

    def _do_publish(self, video_path: Path, meta: dict[str, Any]) -> PublishResult:
        raise NotImplementedError

    # -- shared entry point --------------------------------------------------
    def publish(self, video_path: Path, meta: dict[str, Any]) -> PublishResult:
        video_path = Path(video_path)
        if not video_path.exists():
            return PublishResult(self.name, "error", True, detail=f"missing file {video_path}")

        configured, reason = self._is_configured()

        if not self.cfg.enable_publish:
            msg = f"ENABLE_PUBLISH=false -> dry-run. would upload {video_path.name}"
            if not configured:
                msg += f" (note: {reason})"
            self.log.info(msg)
            return PublishResult(self.name, "dry-run", True, detail=msg)

        if not configured:
            self.log.warning("not configured: %s", reason)
            return PublishResult(self.name, "disabled", True, detail=reason)

        # Retry transient network failures — the scheduled run often fires while
        # the machine is still bringing its connection up after wake/boot.
        attempts = max(1, int(getattr(self.cfg, "publish_retries", 3)))
        delay = 20.0
        last_exc: Exception | None = None
        for i in range(1, attempts + 1):
            try:
                res = self._do_publish(video_path, meta)
                self.log.info("published %s -> %s %s", video_path.name, res.status, res.remote_id)
                return res
            except Exception as exc:  # pragma: no cover - network dependent
                last_exc = exc
                msg = f"{type(exc).__name__}: {exc}".lower()
                transient = any(k in msg for k in _RETRYABLE)
                if transient and i < attempts:
                    self.log.warning("publish attempt %d/%d failed (%s) — retrying in %.0fs",
                                     i, attempts, type(exc).__name__, delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 180)
                    continue
                self.log.exception("publish failed (attempt %d/%d)", i, attempts)
                break
        return PublishResult(self.name, "error", False,
                             detail=f"{type(last_exc).__name__}: {last_exc}")
