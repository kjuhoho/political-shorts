"""Configuration loading: ``.env`` + ``config/sources.yaml`` -> typed objects."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Project root = two levels up from this file (src/political_shorts/config.py).
ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
CONFIG_DIR = ROOT / "config"
SECRETS_DIR = ROOT / "secrets"

for _d in (DATA_DIR, OUTPUT_DIR, LOG_DIR, SECRETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Load .env once, on import. Real environment variables win over the file.
load_dotenv(ROOT / ".env", override=False)


def _get(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return default if val is None else val.strip()


def _get_bool(name: str, default: bool = False) -> bool:
    raw = _get(name, "").lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _resolve(path_str: str) -> str:
    """Make a possibly-relative path absolute against the project root.

    Scheduled tasks run with cwd = C:\\Windows\\System32, so any relative path
    from .env (secrets\\..., assets\\bgm\\...) must be anchored to ROOT. Also
    normalise Windows backslashes -> "/" so the same .env works on Linux
    (GitHub Actions): "secrets\\x.json" would otherwise be one weird filename.
    """
    if not path_str:
        return path_str
    raw = path_str.strip().strip('"')
    if os.sep == "/":                      # POSIX: turn any '\' into '/'
        raw = raw.replace("\\", "/")
    p = Path(raw)
    return str(p if p.is_absolute() else (ROOT / p))


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    lean: str = "center"      # left | center | right | wire
    weight: float = 0.5


@dataclass(frozen=True)
class Settings:
    # general
    timezone: str = "Asia/Seoul"
    collect_window_hours: int = 18
    max_items_per_run: int = 3
    min_sources_for_fact: int = 2
    # don't re-publish a story we've already covered in the last N days
    topic_dedup_days: int = 5
    topic_dedup_threshold: float = 0.6   # keyword-overlap ratio that counts as "same story"
    publish_retries: int = 3             # retry transient network failures on upload
    trending_enabled: bool = True        # re-rank build order by Google Trends KR

    # tts
    enable_tts: bool = True
    tts_provider: str = "edge"          # edge | elevenlabs | azure | gcloud | openai | sapi
    tts_voice: str = ""                 # provider-specific voice name/id (blank = provider default)
    tts_rate: int = 175                 # SAPI words-per-minute-ish; also nudges edge rate
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    google_tts_api_key: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"

    # video
    video_width: int = 1080
    video_height: int = 1920
    video_fps: int = 30
    card_seconds: float = 3.5
    font_path: str = r"C:\Windows\Fonts\malgun.ttf"
    font_bold_path: str = r"C:\Windows\Fonts\malgunbd.ttf"
    font_title: str = ""              # -> assets/fonts/BlackHanSans-Regular.ttf
    font_label: str = ""             # -> assets/fonts/DoHyeon-Regular.ttf
    font_body: str = ""              # -> assets/fonts/Jua-Regular.ttf
    ffmpeg_path: str = "ffmpeg"
    ken_burns: bool = True             # slow zoom/pan on still images
    thumb_enabled: bool = True         # designed opening frame (the Shorts poster)
    thumb_hold_seconds: float = 1.3

    # content style
    headline_style: str = "punchy"    # punchy | neutral
    factcheck_segment: bool = True     # add a fact-check card near the end

    # images (keyless: Wikimedia Commons + Openverse, both CC-licensed)
    image_enabled: bool = True
    image_min_count: int = 4
    image_max_count: int = 6
    image_providers: str = "wikimedia"   # openverse stock tends to be off-topic
    image_cache_dir: str = ""          # resolved to ROOT/assets/cache/images if blank

    # background music (optional)
    bgm_enabled: bool = False
    bgm_path: str = ""
    bgm_credit: str = ""              # attribution line for the description
    bgm_volume_db: float = -26.0
    bgm_duck: bool = True
    bgm_fade_seconds: float = 2.0

    # llm (optional)
    llm_provider: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_model: str = ""

    # dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765

    # publishing
    enable_publish: bool = False
    youtube_client_secret_file: str = ""
    youtube_token_file: str = ""
    youtube_privacy_status: str = "private"
    youtube_category_id: str = "25"
    instagram_enabled: bool = False
    instagram_user_id: str = ""
    instagram_access_token: str = ""
    public_media_base_url: str = ""
    tiktok_enabled: bool = False
    tiktok_access_token: str = ""

    # sources
    feeds: list[Feed] = field(default_factory=list)
    fallback_feeds: list[Feed] = field(default_factory=list)   # apolitical fallback
    extra_keywords: list[str] = field(default_factory=list)

    # paths (not from env)
    root: Path = ROOT
    data_dir: Path = DATA_DIR
    output_dir: Path = OUTPUT_DIR
    log_dir: Path = LOG_DIR
    db_path: Path = DATA_DIR / "political_shorts.sqlite3"

    @property
    def llm_available(self) -> bool:
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return False


def _mk_feeds(items: Any) -> list[Feed]:
    return [
        Feed(
            name=str(f["name"]).strip(),
            url=str(f["url"]).strip(),
            lean=str(f.get("lean", "center")).strip().lower(),
            weight=float(f.get("weight", 0.5)),
        )
        for f in (items or [])
        if f.get("url")
    ]


def _load_sources() -> tuple[list[Feed], list[str], list[Feed]]:
    path = CONFIG_DIR / "sources.yaml"
    if not path.exists():
        return [], [], []
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    feeds = _mk_feeds(raw.get("feeds", []))
    fallback = _mk_feeds(raw.get("fallback_feeds", []))
    extra = [str(k).strip() for k in raw.get("extra_keywords", []) if str(k).strip()]
    return feeds, extra, fallback


def load_settings() -> Settings:
    feeds, extra_keywords, fallback_feeds = _load_sources()
    return Settings(
        fallback_feeds=fallback_feeds,
        timezone=_get("TIMEZONE", "Asia/Seoul"),
        collect_window_hours=_get_int("COLLECT_WINDOW_HOURS", 18),
        max_items_per_run=_get_int("MAX_ITEMS_PER_RUN", 3),
        min_sources_for_fact=_get_int("MIN_SOURCES_FOR_FACT", 2),
        topic_dedup_days=_get_int("TOPIC_DEDUP_DAYS", 5),
        topic_dedup_threshold=_get_float("TOPIC_DEDUP_THRESHOLD", 0.6),
        publish_retries=_get_int("PUBLISH_RETRIES", 3),
        trending_enabled=_get_bool("TRENDING_ENABLED", True),
        thumb_enabled=_get_bool("THUMB_ENABLED", True),
        thumb_hold_seconds=_get_float("THUMB_HOLD_SECONDS", 1.3),
        enable_tts=_get_bool("ENABLE_TTS", True),
        tts_provider=_get("TTS_PROVIDER", "edge").lower(),
        tts_voice=_get("TTS_VOICE", ""),
        tts_rate=_get_int("TTS_RATE", 175),
        elevenlabs_api_key=_get("ELEVENLABS_API_KEY", ""),
        elevenlabs_voice_id=_get("ELEVENLABS_VOICE_ID", ""),
        elevenlabs_model=_get("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        azure_speech_key=_get("AZURE_SPEECH_KEY", ""),
        azure_speech_region=_get("AZURE_SPEECH_REGION", ""),
        google_tts_api_key=_get("GOOGLE_TTS_API_KEY", ""),
        openai_tts_model=_get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        openai_tts_voice=_get("OPENAI_TTS_VOICE", "alloy"),
        video_width=_get_int("VIDEO_WIDTH", 1080),
        video_height=_get_int("VIDEO_HEIGHT", 1920),
        video_fps=_get_int("VIDEO_FPS", 30),
        card_seconds=_get_float("CARD_SECONDS", 3.5),
        font_path=_get("FONT_PATH", r"C:\Windows\Fonts\malgun.ttf"),
        font_bold_path=_get("FONT_BOLD_PATH", r"C:\Windows\Fonts\malgunbd.ttf"),
        font_title=_resolve(_get("FONT_TITLE", "") or "assets/fonts/BlackHanSans-Regular.ttf"),
        font_label=_resolve(_get("FONT_LABEL", "") or "assets/fonts/DoHyeon-Regular.ttf"),
        font_body=_resolve(_get("FONT_BODY", "") or "assets/fonts/Jua-Regular.ttf"),
        ffmpeg_path=_get("FFMPEG_PATH", "ffmpeg"),
        ken_burns=_get_bool("KEN_BURNS", True),
        headline_style=_get("HEADLINE_STYLE", "punchy").lower(),
        factcheck_segment=_get_bool("FACTCHECK_SEGMENT", True),
        image_enabled=_get_bool("IMAGE_ENABLED", True),
        image_min_count=_get_int("IMAGE_MIN_COUNT", 4),
        image_max_count=_get_int("IMAGE_MAX_COUNT", 6),
        image_providers=_get("IMAGE_PROVIDERS", "wikimedia"),
        image_cache_dir=_resolve(_get("IMAGE_CACHE_DIR", "") or "assets/cache/images"),
        bgm_enabled=_get_bool("BGM_ENABLED", False),
        bgm_path=_resolve(_get("BGM_PATH", "")),
        bgm_credit=_get("BGM_CREDIT", ""),
        bgm_volume_db=_get_float("BGM_VOLUME_DB", -26.0),
        bgm_duck=_get_bool("BGM_DUCK", True),
        bgm_fade_seconds=_get_float("BGM_FADE_SECONDS", 2.0),
        llm_provider=_get("LLM_PROVIDER", "").lower(),
        anthropic_api_key=_get("ANTHROPIC_API_KEY", ""),
        openai_api_key=_get("OPENAI_API_KEY", ""),
        llm_model=_get("LLM_MODEL", ""),
        dashboard_host=_get("DASHBOARD_HOST", "127.0.0.1"),
        dashboard_port=_get_int("DASHBOARD_PORT", 8765),
        enable_publish=_get_bool("ENABLE_PUBLISH", False),
        youtube_client_secret_file=_resolve(_get("YOUTUBE_CLIENT_SECRET_FILE", "")),
        youtube_token_file=_resolve(_get("YOUTUBE_TOKEN_FILE", "")),
        youtube_privacy_status=_get("YOUTUBE_PRIVACY_STATUS", "private"),
        youtube_category_id=_get("YOUTUBE_CATEGORY_ID", "25"),
        instagram_enabled=_get_bool("INSTAGRAM_ENABLED", False),
        instagram_user_id=_get("INSTAGRAM_USER_ID", ""),
        instagram_access_token=_get("INSTAGRAM_ACCESS_TOKEN", ""),
        public_media_base_url=_get("PUBLIC_MEDIA_BASE_URL", "").rstrip("/"),
        tiktok_enabled=_get_bool("TIKTOK_ENABLED", False),
        tiktok_access_token=_get("TIKTOK_ACCESS_TOKEN", ""),
        feeds=feeds,
        extra_keywords=extra_keywords,
    )


# Module-level singleton for convenience.
settings = load_settings()
