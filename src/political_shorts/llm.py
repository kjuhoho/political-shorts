"""Thin, optional LLM wrapper. Only used when a provider + key is configured.

The pipeline is fully functional without this module; it exists to *polish*
heuristic output, never to be the sole author of a claim.
"""
from __future__ import annotations

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


# tried in order when LLM_MODEL is unset — Google renames/retires these often,
# and which ones a given free key can see varies, so we walk the list on 404.
_GEMINI_MODELS = [
    "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest",
    "gemini-2.0-flash-001", "gemini-1.5-flash",
]


def _gemini(prompt: str, cfg: Settings, max_tokens: int, system: str) -> str:
    """Google Gemini via the REST API — free tier, no SDK (just requests)."""
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
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": key}, json=body, timeout=40,
        )
        if r.status_code == 200:
            data = r.json()
            cand = (data.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or [{}]
            if len(models) > 1 and model != models[0]:
                log.info("gemini: using model %s", model)
            return "".join(p.get("text", "") for p in parts)
        # surface Google's own reason (SERVICE_DISABLED, API_KEY_*_BLOCKED, ...)
        try:
            err = (r.json().get("error") or {})
            detail = f"{err.get('status', r.status_code)}: {err.get('message', '')}".strip()
        except Exception:
            detail = f"HTTP {r.status_code}"
        last_err = f"{model} -> {detail}"
        if r.status_code != 404:      # 403/400 etc. are key/permission issues, not model
            break
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
