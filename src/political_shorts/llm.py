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


def _gemini(prompt: str, cfg: Settings, max_tokens: int, system: str) -> str:
    """Google Gemini via the REST API — free tier, no SDK (just requests)."""
    key = (getattr(cfg, "gemini_api_key", "") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = cfg.llm_model or "gemini-2.0-flash"
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
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key}, json=body, timeout=40,
    )
    r.raise_for_status()
    data = r.json()
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or [{}]
    return "".join(p.get("text", "") for p in parts)


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
