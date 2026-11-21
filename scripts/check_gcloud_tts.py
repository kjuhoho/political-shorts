"""Check a Google Cloud Text-to-Speech API key before it goes anywhere near the channel.

    python scripts/check_gcloud_tts.py --from-clipboard      # key copied from the Google console (not pasted anywhere)
    python scripts/check_gcloud_tts.py                       # key from GOOGLE_TTS_API_KEY (env or .env)
    python scripts/check_gcloud_tts.py --voice ko-KR-Neural2-C --text "다른 문장"

What it does (the key is never printed or written to disk):
  1. lists the Korean voices this key may use (a free call - nothing is billed),
  2. synthesizes one sentence through the same code the pipeline uses (tts_providers.synth_gcloud, so the speaking
     rate and the character accounting match production) -> output/voice_compare/C_gcloud_<voice>.mp3,
  3. says how many characters that cost and whether Google refused anything (quota / key restriction).
A sentence costs a few dozen characters of the 1,000,000 a month that are free.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

import requests

from political_shorts import tts_providers, tts_usage
from political_shorts.config import settings

SAMPLE = "국민의힘 108석, 지지율 27.3%p 격차... 이 대통령이 21일 오후 2시 30분 회견에서 \"아직 결정 못 했다\"고 밝혔습니다. 그런데 야당은 반발했습니다."
KEY_SHAPE = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")


def _clipboard() -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                         capture_output=True, text=True, encoding="utf-8", timeout=15)
    return (out.stdout or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-clipboard", action="store_true", help="read the key from the clipboard")
    ap.add_argument("--voice", default="", help="default: the pipeline's voice (ko-KR-Neural2-A)")
    ap.add_argument("--text", default=SAMPLE)
    args = ap.parse_args()

    key = _clipboard() if args.from_clipboard else (settings.google_tts_api_key or "").strip()
    if not key:
        print("no key: copy it first and use --from-clipboard, or set GOOGLE_TTS_API_KEY")
        return 2
    if not KEY_SHAPE.match(key):
        print("that is not shaped like a Google API key (AIza + 35 characters) - not sending it anywhere")
        return 2

    r = requests.get("https://texttospeech.googleapis.com/v1/voices", params={"languageCode": "ko-KR", "key": key},
                     timeout=30)
    if r.status_code != 200:
        err = {}
        try:
            err = r.json().get("error", {})
        except ValueError:
            pass
        print(f"voices.list -> HTTP {r.status_code}: {err.get('status', '')} {str(err.get('message', ''))[:200]}")
        print("  403 usually means the Text-to-Speech API is not enabled for the key's project, or the key is "
              "restricted to other APIs; 400 means the key is wrong")
        return 1
    names = sorted(v["name"] for v in r.json().get("voices", []))
    kinds: dict[str, int] = {}
    for n in names:
        k = n.split("-")[2] if n.count("-") >= 2 else "?"
        kinds[k] = kinds.get(k, 0) + 1
    print(f"voices.list OK: {len(names)} Korean voices", dict(sorted(kinds.items())))

    cfg = replace(settings, google_tts_api_key=key, tts_voice=args.voice or "", tts_providers="gcloud")
    out_dir = Path("output") / "voice_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = tts_providers._voice(cfg, "gcloud")
    tts_usage.begin()
    paths = tts_providers.synth_gcloud([args.text], out_dir, cfg)
    att = tts_usage.end()
    if not paths or not paths[0]:
        print(f"synthesis with {voice} failed (HTTP statuses {att.statuses or 'n/a'}; "
              f"quota/key refusal: {att.hard}) - see the warning above")
        return 1
    target = out_dir / f"C_gcloud_{voice}.mp3"
    target.write_bytes(paths[0].read_bytes())
    paths[0].unlink(missing_ok=True)
    print(f"synthesis OK with {voice}: {att.chars} characters billed -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
