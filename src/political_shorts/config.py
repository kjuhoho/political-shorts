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
    tts_providers: str = ""             # "gcloud,edge": tried in this order (blank = TTS_PROVIDER); see tts.py
    tts_caps: str = ""                  # "gcloud:600000,azure:none": monthly character caps (tts_usage.DEFAULT_CAPS)
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    google_tts_api_key: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"

    # voice pipeline (Layer 1 normalize + Layer 3 post, around any engine)
    voice_pipeline: bool = False        # master switch for the normalize/post wrapper
    voice_lufs: float = -14.0           # loudness target for the narration track
    voice_qa: bool = False              # Whisper read-back check (needs faster-whisper)
    voice_qa_model: str = "small"
    voice_qa_threshold: float = 0.72
    # "my voice" engines — TTS_PROVIDER=piper | myvoice
    piper_bin: str = ""                 # path to the piper binary (blank = "piper" on PATH)
    piper_model: str = ""               # path to your trained .onnx voice model
    piper_length_scale: float = 1.0     # global tempo (>1 slower); role hints multiply this
    piper_noise_scale: float = 0.667
    piper_noise_w: float = 0.8
    voice_myvoice_backend: str = "piper"   # piper | xtts_api
    xtts_api_url: str = ""              # self-hosted zero-shot clone endpoint (pre-training)
    xtts_api_token: str = ""
    xtts_speaker_wav_url: str = ""
    xtts_speaker_id: str = ""

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
    video_xfade: bool = True           # cross-dissolve between cards (vs hard cut)
    thumb_enabled: bool = True         # designed opening frame (the Shorts poster)
    thumb_hold_seconds: float = 1.3

    # content style
    headline_style: str = "punchy"    # punchy | neutral
    factcheck_segment: bool = True     # add a fact-check card near the end

    # images (keyless: Wikimedia Commons + Openverse, both CC-licensed)
    image_enabled: bool = True
    image_min_count: int = 4
    image_max_count: int = 6
    hook_quote_first: bool = True
    image_providers: str = "wikimedia"   # openverse stock tends to be off-topic
    image_cache_dir: str = ""          # resolved to ROOT/assets/cache/images if blank

    # b-roll VIDEO on the context/location cards (person cards stay stills).
    # Wikimedia Commons is keyless + CC; Pexels needs a free API key (no per-use
    # cost) and only adds generic stock (flags, assembly, city, ballots).
    # OFF by default — enable once validated so it can't destabilise the live run.
    broll_enabled: bool = False
    # Research stage: after a story is picked, gather extra material (more news,
    # web/official statements, YouTube coverage + comments) so the script is an
    # analysis rather than a re-telling of the seed articles. Best-effort — every
    # source that is unavailable is simply skipped.
    research_enabled: bool = True
    youtube_api_key: str = ""         # YouTube Data API v3 key (search + comments)
    broll_max_count: int = 3
    broll_max_mb: int = 40            # skip anything larger (GHA transcode cost)
    broll_cache_dir: str = ""         # resolved to ROOT/assets/cache/footage if blank
    pexels_api_key: str = ""          # free key; Pexels is the primary b-roll source
    broll_allow_commons: bool = False  # opt-in; Commons KR-politics video is charged

    # background music (optional)
    bgm_enabled: bool = False
    bgm_path: str = ""
    bgm_credit: str = ""              # attribution line for the description
    bgm_volume_db: float = -26.0
    bgm_duck: bool = True
    bgm_fade_seconds: float = 2.0

    # llm (optional) — used ONLY to rewrite the narration into natural,
    # lay-friendly explanation; every fact still traces to the source and the
    # result passes safety.review_script or the template output is kept.
    llm_provider: str = ""            # "" | gemini | anthropic | openai
    gemini_api_key: str = ""          # free tier: aistudio.google.com/apikey
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_model: str = ""               # blank -> per-provider default

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
        tts_providers=_get("TTS_PROVIDERS", "").lower(),
        tts_caps=_get("TTS_CAPS", ""),
        elevenlabs_api_key=_get("ELEVENLABS_API_KEY", ""),
        elevenlabs_voice_id=_get("ELEVENLABS_VOICE_ID", ""),
        elevenlabs_model=_get("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        azure_speech_key=_get("AZURE_SPEECH_KEY", ""),
        azure_speech_region=_get("AZURE_SPEECH_REGION", ""),
        google_tts_api_key=_get("GOOGLE_TTS_API_KEY", ""),
        openai_tts_model=_get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        openai_tts_voice=_get("OPENAI_TTS_VOICE", "alloy"),
        voice_pipeline=_get_bool("VOICE_PIPELINE", False),
        voice_lufs=_get_float("VOICE_LUFS", -14.0),
        voice_qa=_get_bool("VOICE_QA", False),
        voice_qa_model=_get("VOICE_QA_MODEL", "small"),
        voice_qa_threshold=_get_float("VOICE_QA_THRESHOLD", 0.72),
        piper_bin=_resolve(_get("PIPER_BIN", "")) if _get("PIPER_BIN", "") else "",
        piper_model=_resolve(_get("PIPER_MODEL", "")) if _get("PIPER_MODEL", "") else "",
        piper_length_scale=_get_float("PIPER_LENGTH_SCALE", 1.0),
        piper_noise_scale=_get_float("PIPER_NOISE_SCALE", 0.667),
        piper_noise_w=_get_float("PIPER_NOISE_W", 0.8),
        voice_myvoice_backend=_get("VOICE_MYVOICE_BACKEND", "piper").lower(),
        xtts_api_url=_get("XTTS_API_URL", ""),
        xtts_api_token=_get("XTTS_API_TOKEN", ""),
        xtts_speaker_wav_url=_get("XTTS_SPEAKER_WAV_URL", ""),
        xtts_speaker_id=_get("XTTS_SPEAKER_ID", ""),
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
        video_xfade=_get_bool("VIDEO_XFADE", True),
        headline_style=_get("HEADLINE_STYLE", "punchy").lower(),
        factcheck_segment=_get_bool("FACTCHECK_SEGMENT", True),
        image_enabled=_get_bool("IMAGE_ENABLED", True),
        image_min_count=_get_int("IMAGE_MIN_COUNT", 4),
        image_max_count=_get_int("IMAGE_MAX_COUNT", 6),
        hook_quote_first=_get_bool("HOOK_QUOTE_FIRST", True),
        image_providers=_get("IMAGE_PROVIDERS", "wikimedia"),
        image_cache_dir=_resolve(_get("IMAGE_CACHE_DIR", "") or "assets/cache/images"),
        broll_enabled=_get_bool("BROLL_ENABLED", False),
        research_enabled=_get_bool("RESEARCH_ENABLED", True),
        youtube_api_key=_get("YOUTUBE_API_KEY", ""),
        broll_max_count=_get_int("BROLL_MAX_COUNT", 3),
        broll_max_mb=_get_int("BROLL_MAX_MB", 40),
        broll_cache_dir=_resolve(_get("BROLL_CACHE_DIR", "") or "assets/cache/footage"),
        pexels_api_key=_get("PEXELS_API_KEY", ""),
        broll_allow_commons=_get_bool("BROLL_ALLOW_COMMONS", False),
        bgm_enabled=_get_bool("BGM_ENABLED", False),
        bgm_path=_resolve(_get("BGM_PATH", "")),
        bgm_credit=_get("BGM_CREDIT", ""),
        bgm_volume_db=_get_float("BGM_VOLUME_DB", -26.0),
        bgm_duck=_get_bool("BGM_DUCK", True),
        bgm_fade_seconds=_get_float("BGM_FADE_SECONDS", 2.0),
        llm_provider=_get("LLM_PROVIDER", "").lower(),
        gemini_api_key=_get("GEMINI_API_KEY", ""),
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
