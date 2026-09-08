"""Thin, optional LLM wrapper. Only used when a provider + key is configured.

The pipeline is fully functional without this module; it exists to *polish*
heuristic output, never to be the sole author of a claim.
"""
from __future__ import annotations

import time

import requests

from .config import Settings
from .logging_setup import get_logger

log = get_logger("llm")


def complete(prompt: str, cfg: Settings, max_tokens: int = 400, system: str = "") -> str:
    provider = cfg.llm_provider
    if provider == "gemini":
        return _gemini(prompt, cfg, max_tokens, system)
    if provider == "anthropic":
        return _anthropic(prompt, cfg, max_tokens, system)
    if provider == "openai":
        return _openai(prompt, cfg, max_tokens, system)
    raise RuntimeError(f"no usable LLM provider configured (LLM_PROVIDER={provider!r})")


# tried in order when LLM_MODEL is unset. `gemini-flash-latest` is a portable
# alias; the "lite" models carry far more free-tier capacity, so they come
# before the in-demand full flash models — on a 404 (not visible to this key)
# OR a sustained 503/"high demand" we just move to the next one.
_GEMINI_MODELS = [
    "gemini-flash-latest", "gemini-2.0-flash", "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite", "gemini-2.5-flash", "gemini-1.5-flash",
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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        status = None
        for attempt in range(2):
            r = requests.post(url, params={"key": key}, json=body, timeout=40)
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
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=cfg.openai_api_key)
    model = cfg.llm_model or "gpt-4o-mini"
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system or "You are a careful, neutral Korean news editor."},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content or ""
