"""Thin, optional LLM wrapper. Only used when a provider + key is configured.

The pipeline is fully functional without this module; it exists to *polish*
heuristic output, never to be the sole author of a claim.
"""
from __future__ import annotations

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


def web_search(prompt: str, cfg: Settings, max_tokens: int = 1800, accept=None) -> str:
    """One question answered with LIVE web search. Groq's `groq/compound` does
    the searching itself; if it is unavailable or rate-limited, Gemini with the
    Google Search tool is tried. Returns the answer text ("" if neither worked) —
    callers treat the text as unverified research notes, never as ground truth.
    `accept(text) -> bool` lets the caller reject an answer that has no real
    content (e.g. an empty JSON skeleton) so the next model/provider is tried."""
    errs: list[str] = []
    gkey = (getattr(cfg, "groq_api_key", "") or os.environ.get("GROQ_API_KEY", "")).strip()
    if gkey:
        for model in ("groq/compound", "groq/compound-mini"):
            try:
                r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                                  headers={"Authorization": f"Bearer {gkey}"},
                                  json={"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                                        "messages": [{"role": "user", "content": prompt}]},
                                  timeout=(10, 120))
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
    mkey = (getattr(cfg, "gemini_api_key", "") or "").strip()
    if mkey:
        for model in ("gemini-2.5-flash", "gemini-flash-latest"):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": mkey},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "tools": [{"google_search": {}}],
                          "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2}},
                    timeout=(10, 120))
                if r.status_code == 200:
                    cand = (r.json().get("candidates") or [{}])[0]
                    parts = (cand.get("content") or {}).get("parts") or []
                    txt = "".join(p.get("text", "") for p in parts).strip()
                    if txt and (accept is None or accept(txt)):
                        log.info("web_search: answered by %s (%d chars)", model, len(txt))
                        return txt
                    errs.append(f"{model} -> 200 but {'empty' if not txt else 'no usable content'}")
                    continue
                errs.append(f"{model} -> {r.status_code}")
            except Exception as exc:  # pragma: no cover - network dependent
                errs.append(f"{model} -> {str(exc)[:60]}")
    log.info("web_search: no answer (%s)", "; ".join(errs) or "no key")
    return ""


# Tried in this order after the configured provider fails (free tiers first).
_FALLBACK_ORDER = ["groq", "gemini", "openai", "anthropic"]


def complete(prompt: str, cfg: Settings, max_tokens: int = 400, system: str = "") -> str:
    provider = cfg.llm_provider
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


# Groq — FREE, no credit card, and far steadier than the Gemini free tier.
# OpenAI-compatible endpoint. Models tried in order when LLM_MODEL is unset.
_GROQ_MODELS = [
    "openai/gpt-oss-120b", "openai/gpt-oss-20b",
]


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


def _groq(prompt: str, cfg: Settings, max_tokens: int, system: str) -> str:
    key = (getattr(cfg, "groq_api_key", "") or os.environ.get("GROQ_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    models = [cfg.llm_model] if cfg.llm_model else list(_GROQ_MODELS)
    last = ""
    for model in models:
        if _cooling(f"groq:{model}"):     # daily/long limit already hit — don't burn retries on it
            last = f"{model} -> cooling down"
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
            if status == 200:
                if model != models[0]:
                    log.info("groq: using model %s", model)
                return (r.json()["choices"][0]["message"]["content"] or "").strip()
            try:
                last = f"{model} -> {status}: {(r.json().get('error') or {}).get('message', '')}".strip()
            except Exception:
                last = f"{model} -> HTTP {status}"
            if status == 429:
                wait = _retry_after(last)
                if wait > 20:
                    # "try again in 40m38s" is a per-day/long limit: retrying for a few
                    # seconds is pointless, so sit this model out and move on at once
                    _COOLDOWN[f"groq:{model}"] = time.time() + min(wait, 3600.0)
                    log.info("groq: %s rate-limited for ~%.0fs — cooling it down", model, wait)
                    break
            if status in (429, 500, 502, 503) and attempt < 2:
                time.sleep(3.0)
                continue
            break
        if status in (400, 404, 429):     # retired / not visible / rate-limited -> next model
            log.info("groq: model %s skipped (%s)", model, last)
            continue
        break
    raise RuntimeError(f"groq call failed ({last})")


# tried in order when LLM_MODEL is unset. `gemini-flash-latest` is a portable
# alias; the "lite" models carry far more free-tier capacity, so they come
# before the in-demand full flash models — on a 404 (not visible to this key)
# OR a sustained 503/"high demand" we just move to the next one.
_GEMINI_MODELS = [
    "gemini-flash-latest", "gemini-2.0-flash-lite", "gemini-2.5-flash-lite",
    "gemini-2.0-flash", "gemini-2.5-flash", "gemini-flash-lite-latest",
]


def _gemini(prompt: str, cfg: Settings, max_tokens: int, system: str) -> str:
    """Google Gemini via the REST API — free tier, no SDK (just requests).
    Walks a model list on 404; retries once on a transient 429/5xx/UNAVAILABLE."""
    key = (getattr(cfg, "gemini_api_key", "") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.45,
            "responseMimeType": "application/json",
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    models = [cfg.llm_model] if cfg.llm_model else list(_GEMINI_MODELS)
    last_err = ""
    for model in models:
        if _cooling(f"gemini:{model}"):    # not visible to this key / long rate limit: skip it
            continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        status = None
        for attempt in range(2):
            r = requests.post(url, params={"key": key}, json=body, timeout=(10, 75))
            status = r.status_code
            if status == 200:
                data = r.json()
                cand = (data.get("candidates") or [{}])[0]
                parts = (cand.get("content") or {}).get("parts") or [{}]
                if len(models) > 1 and model != models[0]:
                    log.info("gemini: using model %s", model)
                return "".join(p.get("text", "") for p in parts)
            try:
                err = r.json().get("error") or {}
                detail = f"{err.get('status', status)}: {err.get('message', '')}".strip()
            except Exception:
                detail = f"HTTP {status}"
            last_err = f"{model} -> {detail}"
            if status == 404:                 # a model this key can't see never appears mid-run
                _COOLDOWN[f"gemini:{model}"] = time.time() + 6 * 3600.0
            elif status == 429 and _retry_after(detail) > 20:
                _COOLDOWN[f"gemini:{model}"] = time.time() + min(_retry_after(detail), 3600.0)
                break
            if status in (429, 500, 503) and attempt == 0:
                time.sleep(3.0)               # transient capacity blip — one retry
                continue
            break
        if status in (404, 429, 500, 503):
            continue          # not visible / overloaded on this model — try the next
        break                 # 403 / 400 -> key or request problem, stop
    raise RuntimeError(f"gemini call failed ({last_err})")


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
