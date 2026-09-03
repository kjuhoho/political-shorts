"""Publish adapters. Every adapter honours ``ENABLE_PUBLISH``:
when it is false, ``publish()`` returns a PublishResult with status="dry-run"
and performs no network write.
"""
from __future__ import annotations

from .base import Publisher, PublishResult
from .instagram import InstagramPublisher
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher

__all__ = [
    "Publisher",
    "PublishResult",
    "YouTubePublisher",
    "InstagramPublisher",
    "TikTokPublisher",
    "get_publishers",
]


def get_publishers(cfg=None):
    """Return the list of publisher instances that are switched on."""
    from ..config import settings as _settings

    cfg = cfg or _settings
    pubs: list[Publisher] = [YouTubePublisher(cfg)]
    if cfg.instagram_enabled:
        pubs.append(InstagramPublisher(cfg))
    if cfg.tiktok_enabled:
        pubs.append(TikTokPublisher(cfg))
    return pubs
