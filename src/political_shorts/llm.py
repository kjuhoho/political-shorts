"""Thin, optional LLM wrapper. Only used when a provider + key is configured.

The pipeline is fully functional without this module; it exists to *polish*
heuristic output, never to be the sole author of a claim.
"""
from __future__ import annotations

import collections
import dataclasses
import os
import re
import time

import requests

from .config import Settings
from .logging_setup import get_logger

log = get_logger("llm")


def _call(provider: str, prompt: str, cfg: Settings, max_tokens: int, system: str) -> str:
    if provider == "groq":
        return _groq(prompt, cfg, max_tokens, system)
    if provider == "gemini":
        return _gemini(prompt, cfg, max_tokens, system)
    if provider == "anthropic":
        return _anthropic(prompt, cfg, max_tokens, system)
    if provider == "openai":
        return _openai(prompt, cfg, max_tokens, system)
    raise RuntimeError(f"no usable LLM provider configured (LLM_PROVIDER={provider!r})")


def _has_key(provider: str, cfg: Settings) -> bool:
    if provider == "groq":
        return bool((getattr(cfg, "groq_api_key", "") or os.environ.get("GROQ_API_KEY", "")).strip())
    return bool((getattr(cfg, f"{provider}_api_key", "") or "").strip())


def _web_search_groq(prompt: str, cfg: Settings, max_tokens: int, accept, errs: list[str]) -> str:
    """groq/compound answers with its own live search; '' when unavailable."""
    gkey = (getattr(cfg, "groq_api_key", "") or os.environ.get("GROQ_API_KEY", "")).strip()
    if gkey:
        for model in ("groq/compound", "groq/compound-mini"):
            try:
                r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                                  headers={"Authorization": f"Bearer {gkey}"},
                                  json={"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                                        "messages": [{"role": "user", "content": prompt}]},
                                  timeout=(10, 120))
                USAGE[f"groq-search:{model}:{r.status_code}"] += 1
                if r.status_code == 200:
                    txt = (r.json()["choices"][0]["message"]["content"] or "").strip()
                    if txt and (accept is None or accept(txt)):
                        log.info("web_search: answered by %s (%d chars)", model, len(txt))
                        return txt
                    errs.append(f"{model} -> 200 but {'empty' if not txt else 'no usable content'}")
                    continue
                errs.append(f"{model} -> {r.status_code}")
            except Exception as exc:  # pragma: no cover - network dependent
                errs.append(f"{model} -> {str(exc)[:60]}")
    return ""


def _web_search_gemini(prompt: str, cfg: Settings, max_tokens: int, accept, errs: list[str]) -> str:
    """Gemini with the Google Search tool; '' when unavailable."""
    mkey = (getattr(cfg, "gemini_api_key", "") or "").strip()
    if mkey:
        for model in _GEMINI_FLASH:
            if _cooling(f"gemini:{model}"):
                errs.append(f"{model} -> cooling down")
                continue
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": mkey},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "tools": [{"google_search": {}}],
                          "generationConfig": _gemini_generation(model, max_tokens, temperature=0.2)},
                    timeout=(10, 120))
                USAGE[f"gemini-search:{model}:{r.status_code}"] += 1
                if r.status_code != 200 and _gemini_rejected(model, r.status_code, getattr(r, "text", "") or "", ""):
                    errs.append(f"{model} -> {r.status_code}")
                    continue
                if r.status_code == 200:
                    cand = (r.json().get("candidates") or [{}])[0]
                    parts = (cand.get("content") or {}).get("parts") or []
                    txt = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                    if txt and (accept is None or accept(txt)):
                        log.info("web_search: answered by %s (%d chars)", model, len(txt))
                        return txt
                    errs.append(f"{model} -> 200 but {'empty' if not txt else 'no usable content'}")
                    continue
                errs.append(f"{model} -> {r.status_code}")
            except Exception as exc:  # pragma: no cover - network dependent
                errs.append(f"{model} -> {str(exc)[:60]}")
    return ""


# Gemini first for every shorts call (user, 2026-09-22): the Groq free tier is shared with the longform
# workflow and was already hitting 413/429; Groq stays as the fallback.
_WEB_SEARCH = {"gemini": _web_search_gemini, "groq": _web_search_groq}
_WEB_SEARCH_ORDER = ["gemini", "groq"]


def web_search(prompt: str, cfg: Settings, max_tokens: int = 1800, accept=None) -> str:
    """One question answered with LIVE web search, by the providers in _WEB_SEARCH_ORDER: Gemini with the
    Google Search tool first, then Groq's `groq/compound` (which does the searching itself). Returns the
    answer text ("" if neither worked) — callers treat the text as unverified research notes, never as ground truth.
    `accept(text) -> bool` lets the caller reject an answer that has no real
    content (e.g. an empty JSON skeleton) so the next model/provider is tried."""
    errs: list[str] = []
    for provider in _WEB_SEARCH_ORDER:
        txt = _WEB_SEARCH[provider](prompt, cfg, max_tokens, accept, errs)
        if txt:
            return txt
    log.info("web_search: no answer (%s)", "; ".join(errs) or "no key")
    return ""


# Tried in this order after the configured provider fails (free tiers first, Gemini before Groq).
_FALLBACK_ORDER = ["gemini", "groq", "openai", "anthropic"]


def complete(prompt: str, cfg: Settings, max_tokens: int = 400, system: str = "") -> str:
    provider = cfg.llm_provider
    if provider == "gemini" and not cfg.llm_model:
        return _tiered(prompt, cfg, max_tokens, system)
    try:
        return _call(provider, prompt, cfg, max_tokens, system)
    except Exception as exc:
        if provider not in _FALLBACK_ORDER:
            raise
        first = exc
    # LLM_MODEL names a model of the configured provider only — blank it so
    # each fallback provider walks its own default model list.
    alt_cfg = dataclasses.replace(cfg, llm_model="")
    for alt in _FALLBACK_ORDER:
        if alt == provider or not _has_key(alt, cfg):
            continue
        log.info("llm: %s failed (%s) — falling back to %s", provider, str(first)[:120], alt)
        try:
            return _call(alt, prompt, alt_cfg, max_tokens, system)
        except Exception as exc:
            log.info("llm: fallback %s failed too (%s)", alt, str(exc)[:120])
    raise first


# Groq — FREE, OpenAI-compatible endpoint. Only the 120b model (user, 2026-09-22): the Groq free quota is per
# organisation AND per model, the longform workflow uses the same organisation with other models (20b, llama,
# qwen), so the shorts stay off those to leave the longform's quota alone.
_GROQ_MODELS = ["openai/gpt-oss-120b"]


# ------------------------------------------------------------------------------------------------ tiers
# User rule (2026-09-22): Gemini first, and no wasted quota. The free Gemini Flash models allow only ~20 requests
# a day each, so: every Flash model first, then Groq's 120b, and the Flash-Lite models only when both are spent.
def _tiers() -> list[tuple[str, list[str]]]:
    return [("gemini", list(_GEMINI_FLASH)), ("groq", list(_GROQ_MODELS)), ("gemini", list(_GEMINI_LITE))]


def _tiered(prompt: str, cfg: Settings, max_tokens: int, system: str) -> str:
    errors: list[str] = []
    for provider, models in _tiers():
        if not _has_key(provider, cfg):
            continue
        live = [m for m in models if not _cooling(f"{provider}:{m}")]
        if not live:                           # every model of this tier is spent for this run: no request at all
            errors.append(f"{provider}:{'/'.join(models)} cooling down")
            continue
        try:
            fn = _gemini if provider == "gemini" else _groq
            return fn(prompt, cfg, max_tokens, system, models=live)
        except Exception as exc:
            errors.append(str(exc)[:160])
            log.info("llm: tier %s:%s failed (%s) — next tier", provider, live[0], str(exc)[:120])
    for alt in ("openai", "anthropic"):         # paid providers, only when a key exists
        if _has_key(alt, cfg):
            try:
                return _call(alt, prompt, dataclasses.replace(cfg, llm_model=""), max_tokens, system)
            except Exception as exc:
                errors.append(str(exc)[:160])
    raise RuntimeError("every LLM tier failed: " + " | ".join(errors))


# ----------------------------------------------------------------------------------------------- usage
# requests actually sent this run, per provider:model and outcome — logged at the end of a run so waste is visible
USAGE: collections.Counter = collections.Counter()


def usage_summary() -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(USAGE.items())) or "none"


# model/provider -> unix time until which it is skipped (set when a 429 says the wait is long)
_COOLDOWN: dict[str, float] = {}


def _cooling(name: str) -> bool:
    return time.time() < _COOLDOWN.get(name, 0.0)


def _retry_after(message: str) -> float:
    """Seconds from an API rate-limit message like "Please try again in 40m38.64s" /
    "…in 1h2m3s" / "…in 12.5s" / "…in 340ms". 0 when there is none."""
    m = re.search(r"(?:try again|retry) in\s+((?:\d+(?:\.\d+)?\s*(?:h|m(?!s)|s|ms)\s*)+)", message or "", re.I)
    if not m:
        return 0.0
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)", m.group(1), re.I):
        total += float(num) * {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}[unit.lower()]
    return total


# The free tier caps one request at 8,000 tokens a minute (prompt + answer): a bigger request answers 413 and can
# never succeed. The first 413 records the size; a request at least that big is not sent again this run.
_GROQ_TOO_BIG: dict[str, int] = {}


def _groq(prompt: str, cfg: Settings, max_tokens: int, system: str, models: list[str] | None = None) -> str:
    key = (getattr(cfg, "groq_api_key", "") or os.environ.get("GROQ_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    models = models or ([cfg.llm_model] if cfg.llm_model else list(_GROQ_MODELS))
    size = len(prompt) + len(system) + 4 * max_tokens
    last = ""
    for model in models:
        if _cooling(f"groq:{model}"):     # daily/long limit already hit — don't burn retries on it
            last = f"{model} -> cooling down"
            continue
        if size >= _GROQ_TOO_BIG.get(model, 1 << 30):
            last = f"{model} -> request too large for the free tier (skipped, no request sent)"
            continue
        body = {
            "model": model, "max_tokens": max_tokens, "temperature": 0.5,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system or "You are a careful, neutral Korean news editor."},
                {"role": "user", "content": prompt},
            ],
        }
        if model.startswith("openai/gpt-oss"):
            # reasoning model: keep thinking short and leave room for the answer
            body["reasoning_effort"] = "low"
            body["max_tokens"] = max_tokens + 1024
        status = None
        for attempt in range(3):
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers={"Authorization": f"Bearer {key}"},
                              json=body, timeout=(10, 75))
            status = r.status_code
            USAGE[f"groq:{model}:{status}"] += 1
            if status == 200:
                if model != models[0]:
                    log.info("groq: using model %s", model)
                return (r.json()["choices"][0]["message"]["content"] or "").strip()
            try:
                last = f"{model} -> {status}: {(r.json().get('error') or {}).get('message', '')}".strip()
            except Exception:
                last = f"{model} -> HTTP {status}"
            if status == 413:
                _GROQ_TOO_BIG[model] = min(size, _GROQ_TOO_BIG.get(model, 1 << 30))
                break
            if status == 429:
                # a per-minute or per-day limit: retrying after 3 s only collects more 429s, so sit this model
                # out for the wait Groq names (at least 20 s) and let the next tier answer
                wait = max(_retry_after(last), 20.0)
                _COOLDOWN[f"groq:{model}"] = time.time() + min(wait, 3600.0)
                log.info("groq: %s rate-limited for ~%.0fs — cooling it down", model, wait)
                break
            if status in (500, 502, 503) and attempt < 2:
                time.sleep(3.0)
                continue
            break
        if status in (400, 404, 429):     # retired / not visible / rate-limited -> next model
            log.info("groq: model %s skipped (%s)", model, last)
            continue
        break
    raise RuntimeError(f"groq call failed ({last})")


# Probed with the channel's key on 2026-09-22 (gemini-models workflow): the 2.0/2.5 models answer 404 ("no longer
# available to new users"); the free tier allows ~20 requests a day per Flash model
# (GenerateRequestsPerDayPerProjectPerModel-FreeTier). The full Flash models write better; Flash-Lite is the last
# resort (see _tiers).
_GEMINI_FLASH = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest"]
_GEMINI_LITE = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
_GEMINI_MODELS = _GEMINI_FLASH + _GEMINI_LITE

# Gemini 3 models "think" before answering, and the thinking tokens count against maxOutputTokens: with the
# default level, gemini-3.5-flash spent 673 of a 700-token budget thinking and returned 11 tokens of broken JSON
# (probe, 2026-09-22) — every story analysis and every AI review failed that way and was retried 3x for nothing.
# So thinking is kept low, and the budget gets headroom for it.
_GEMINI_THINKING = "low"
_THINKING_HEADROOM = 1024
_NO_THINKING_CFG: set[str] = set()       # models that rejected thinkingConfig (400) — sent without it afterwards


def _gemini_thinks(model: str) -> bool:
    return model not in _NO_THINKING_CFG and (model.startswith("gemini-3") or model.endswith("-latest"))


def _gemini_generation(model: str, max_tokens: int, **extra) -> dict:
    gen = {"maxOutputTokens": max_tokens, **extra}
    if _gemini_thinks(model):
        gen["maxOutputTokens"] = max_tokens + _THINKING_HEADROOM
        gen["thinkingConfig"] = {"thinkingLevel": _GEMINI_THINKING}
    return gen


def _seconds_to_quota_reset(now: float | None = None) -> float:
    """Gemini's per-day quotas reset at midnight Pacific time (07:00/08:00 UTC); 07:00 UTC is the earlier bound."""
    now = time.time() if now is None else now
    day = 86400.0
    return (7 * 3600.0 - now) % day or day


def _gemini_rejected(model: str, status: int, raw: str, detail: str) -> bool:
    """Book-keeping for a failed Gemini request. True when the model should not be asked again right now:
       404        not visible to this key            -> skip it for 6 h
       429/day    the model's daily quota is spent    -> skip it until the quota resets (no retry: it cannot work)
       429/other  per-minute limit                    -> skip it for the delay Google names (at least 30 s)"""
    if status == 404:
        _COOLDOWN[f"gemini:{model}"] = time.time() + 6 * 3600.0
        return True
    if status == 429:
        if "PerDay" in raw or "per day" in raw.lower():
            _COOLDOWN[f"gemini:{model}"] = time.time() + _seconds_to_quota_reset()
        else:
            m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', raw)
            wait = float(m.group(1)) if m else _retry_after(detail)
            _COOLDOWN[f"gemini:{model}"] = time.time() + min(max(wait, 30.0), 3600.0)
        return True
    return False


def _gemini(prompt: str, cfg: Settings, max_tokens: int, system: str, models: list[str] | None = None) -> str:
    """Google Gemini via the REST API — free tier, no SDK (just requests). Walks `models`; a model whose quota is
    spent or that is overloaded is skipped (and remembered) instead of being retried."""
    key = (getattr(cfg, "gemini_api_key", "") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    models = models or ([cfg.llm_model] if cfg.llm_model else list(_GEMINI_MODELS))
    last_err = ""
    for model in models:
        if _cooling(f"gemini:{model}"):    # not visible / quota spent / overloaded: no request at all
            continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        status = None
        for attempt in range(2):
            body: dict = {"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": _gemini_generation(model, max_tokens, temperature=0.45,
                                                                 responseMimeType="application/json")}
            if system:
                body["systemInstruction"] = {"parts": [{"text": system}]}
            r = requests.post(url, params={"key": key}, json=body, timeout=(10, 75))
            status = r.status_code
            USAGE[f"gemini:{model}:{status}"] += 1
            if status == 200:
                data = r.json()
                cand = (data.get("candidates") or [{}])[0]
                parts = (cand.get("content") or {}).get("parts") or [{}]
                if cand.get("finishReason") == "MAX_TOKENS":
                    u = data.get("usageMetadata") or {}
                    log.warning("gemini: %s hit the token limit (thinking %s, answer %s) — the answer may be cut",
                                model, u.get("thoughtsTokenCount", 0), u.get("candidatesTokenCount", 0))
                if model != models[0]:
                    log.info("gemini: using model %s", model)
                return "".join(p.get("text", "") for p in parts if not p.get("thought"))
            raw = getattr(r, "text", "") or ""
            try:
                err = r.json().get("error") or {}
                detail = f"{err.get('status', status)}: {err.get('message', '')}".strip()
            except Exception:
                detail = f"HTTP {status}"
            last_err = f"{model} -> {detail}"
            if status == 400 and "thinking" in detail.lower() and model not in _NO_THINKING_CFG:
                _NO_THINKING_CFG.add(model)   # this model does not take thinkingConfig: ask once more without it
                continue
            if _gemini_rejected(model, status, raw, detail):
                break
            if status in (500, 503) and attempt == 0:
                time.sleep(3.0)               # transient capacity blip — one retry
                continue
            if status == 503:                 # still overloaded: leave it alone for a couple of minutes
                _COOLDOWN[f"gemini:{model}"] = time.time() + 120.0
            break
        if status in (404, 429, 500, 503):
            log.info("gemini: model %s skipped (%s)", model, last_err[:160])
            continue          # not visible / overloaded on this model — try the next
        break                 # 403 / 400 -> key or request problem, stop
    raise RuntimeError(f"gemini call failed ({last_err or 'every model cooling down'})")


def _anthropic(prompt: str, cfg: Settings, max_tokens: int, system: str) -> str:
    import anthropic  # type: ignore

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    model = cfg.llm_model or "claude-sonnet-5"
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or "You are a careful, neutral Korean news editor.",
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(getattr(b, "text", "") for b in msg.content)


def _openai(prompt: str, cfg: Settings, max_tokens: int, system: str) -> str:
    """OpenAI chat completions via REST (no SDK dependency). gpt-4o-mini is
    cheap and far more reliable at instruction-following than the Gemini free
    tier — a couple of cents a month at ~2 videos/day."""
    key = (cfg.openai_api_key or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")
    model = cfg.llm_model or "gpt-4o-mini"
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system or "You are a careful, neutral Korean news editor."},
            {"role": "user", "content": prompt},
        ],
    }
    last = ""
    for attempt in range(3):
        r = requests.post("https://api.openai.com/v1/chat/completions",
                          headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
        if r.status_code == 200:
            return (r.json()["choices"][0]["message"]["content"] or "").strip()
        try:
            last = f"{r.status_code}: {(r.json().get('error') or {}).get('message', '')}"
        except Exception:
            last = f"HTTP {r.status_code}"
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            time.sleep(3.0)
            continue
        break
    raise RuntimeError(f"openai call failed ({last})")
