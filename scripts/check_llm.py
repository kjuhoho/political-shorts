"""Quick check that the configured LLM key works.

Usage:  python scripts/check_llm.py
Reads GEMINI_API_KEY / LLM_PROVIDER from .env (via the normal config load).
Prints which Gemini models the key can see and runs one tiny generateContent
call. Nothing is published; this only talks to the model API.
"""
from __future__ import annotations

import sys

import requests

from political_shorts.config import settings


def main() -> int:
    prov = (settings.llm_provider or "").strip() or "(unset)"
    print(f"LLM_PROVIDER = {prov}")
    key = (getattr(settings, "gemini_api_key", "") or "").strip()
    if not key:
        print("GEMINI_API_KEY is empty — add it to .env")
        return 1
    print(f"GEMINI_API_KEY = ...{key[-4:]} (len {len(key)})")

    # 1) list models
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key}, timeout=30,
        )
        r.raise_for_status()
        models = [
            m["name"].split("/")[-1]
            for m in r.json().get("models", [])
            if "generateContent" in (m.get("supportedGenerationMethods") or [])
        ]
        print(f"\nmodels available for generateContent ({len(models)}):")
        for m in models:
            print(f"  - {m}")
    except Exception as exc:
        print(f"\ncould not list models: {exc}")
        print("=> the key is probably invalid, or the Generative Language API "
              "is not enabled for its project.")
        return 2

    # 2) tiny real call through the same code the pipeline uses
    from political_shorts.llm import _gemini

    try:
        out = _gemini('한 단어로 답: 하늘은 무슨 색?', settings, 20, "")
        print(f"\ntest call OK -> {out.strip()[:80]!r}")
        return 0
    except Exception as exc:
        print(f"\ntest call FAILED: {exc}")
        pref = models[0] if models else "gemini-2.5-flash"
        print(f"=> set  LLM_MODEL={pref}  in .env and re-run.")
        return 3


if __name__ == "__main__":
    sys.exit(main())
