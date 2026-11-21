"""Shared helpers behind ``scripts/voice_*.py`` and ``political_shorts voice …``.

``preview`` — run text through Layer 1 → engine → Layer 3 and return the wavs.
``doctor``  — check every link in the "my voice" chain and print what's missing.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

from ..config import Settings, settings
from ..logging_setup import get_logger
from . import normalize

log = get_logger("voice.tools")

ROLES = ["hook", "summary", "what", "reaction", "factcheck", "outro"]


def preview(
    text: str,
    *,
    roles: list[str] | None = None,
    engine: str = "",
    raw: bool = False,
    cfg: Settings | None = None,
    out_dir: Path | None = None,
) -> list[tuple[str, Path | None, float]]:
    """Synthesize *text* once per role. Returns [(role, wav_path|None, seconds)]."""
    cfg = cfg or settings
    roles = roles or ["what"]
    out_dir = out_dir or (cfg.output_dir / "voice_preview")
    out_dir.mkdir(parents=True, exist_ok=True)

    run_cfg = replace(cfg, enable_tts=True, voice_pipeline=not raw)
    if engine:
        run_cfg = replace(run_cfg, tts_provider=engine.lower())

    from ..tts import synthesize_segments

    ns = synthesize_segments([text] * len(roles), out_dir, run_cfg, roles=roles)
    results: list[tuple[str, Path | None, float]] = []
    for role, n in zip(roles, ns):
        if not n.wav_path:
            results.append((role, None, 0.0))
            continue
        dst = out_dir / f"preview_{run_cfg.tts_provider}_{role}{Path(n.wav_path).suffix}"
        Path(n.wav_path).replace(dst)
        results.append((role, dst, n.duration_s))
    return results


def _ok(cond: bool) -> str:
    return "ok" if cond else "— MISSING"


def doctor(cfg: Settings | None = None) -> int:
    """Print a readiness report for the voice pipeline. Returns 0 if usable."""
    cfg = cfg or settings
    print("political-shorts  voice doctor\n" + "-" * 40)

    ff = cfg.ffmpeg_path or "ffmpeg"
    ff_ok = bool(shutil.which(ff) or Path(ff).exists())
    print(f"  ffmpeg              : {ff}  ({_ok(ff_ok)})")

    print(f"  VOICE_PIPELINE      : {cfg.voice_pipeline}")
    print(f"  VOICE_LUFS          : {cfg.voice_lufs}")
    print(f"  TTS_PROVIDER        : {cfg.tts_provider}")

    # Layer 1
    try:
        sample = normalize.prepare("정부는 2026년 27.3%p 인상을 발표했습니다", "what")
        print(f"  Layer 1 normalize   : ok  e.g. {sample.text!r}")
    except Exception as exc:
        print(f"  Layer 1 normalize   : FAIL ({exc})")

    from .lexicon import _entries

    try:
        print(f"  lexicon entries     : {len(_entries())}  (config/voice_lexicon.yaml"
              f"{' + .local' if (cfg.root / 'config' / 'voice_lexicon.local.yaml').exists() else ''})")
    except Exception as exc:
        print(f"  lexicon             : FAIL ({exc})")

    # edge (the always-available fallback)
    try:
        import edge_tts  # noqa: F401
        print("  edge-tts            : ok (free fallback)")
    except Exception:
        print("  edge-tts            : — not importable (pip install edge-tts)")

    # Layer 2 — piper (the "my voice" engine)
    pbin = getattr(cfg, "piper_bin", "") or "piper"
    pbin_ok = bool(shutil.which(pbin) or Path(pbin).exists())
    print(f"  PIPER_BIN           : {pbin}  ({_ok(pbin_ok)})")
    pmodel = getattr(cfg, "piper_model", "")
    if pmodel:
        mp = Path(pmodel)
        cfg_json = mp.with_suffix(mp.suffix + ".json")
        alt_json = mp.with_suffix(".onnx.json")
        print(f"  PIPER_MODEL         : {mp}  ({_ok(mp.exists())})")
        print(f"  model config .json  : {_ok(cfg_json.exists() or alt_json.exists())}")
    else:
        print("  PIPER_MODEL         : (unset — train one, see tools/voice/README.md)")

    if pbin_ok and pmodel and Path(pmodel).exists():
        try:
            t = normalize.prepare("음성 점검용 문장입니다", "what")
            wav = cfg.output_dir / "voice_preview" / "_doctor.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [shutil.which(pbin) or pbin, "-m", str(pmodel), "-f", str(wav)],
                input=t.text.encode("utf-8"), capture_output=True, timeout=60, check=True,
            )
            print(f"  piper synth test    : {'ok -> ' + str(wav) if wav.exists() else 'produced nothing'}")
        except Exception as exc:
            print(f"  piper synth test    : FAIL ({exc})")

    # QA (optional, side venv)
    try:
        import faster_whisper  # noqa: F401
        print(f"  faster-whisper (QA) : ok  (VOICE_QA={cfg.voice_qa})")
    except Exception:
        print(f"  faster-whisper (QA) : — not in this interpreter (optional; VOICE_QA={cfg.voice_qa})")

    # recordings / dataset progress
    rec = cfg.root / "recordings"
    ds = cfg.root / "dataset"
    n_rec = len(list(rec.glob("*.wav"))) + len(list(rec.glob("*.m4a"))) + len(list(rec.glob("*.mp3"))) if rec.exists() else 0
    print(f"  recordings/          : {n_rec} file(s)")
    print(f"  dataset/metadata.csv : {_ok((ds / 'metadata.csv').exists())}")

    usable = ff_ok and (pbin_ok and pmodel and Path(pmodel).exists() or True)
    print("-" * 40)
    if pbin_ok and pmodel and Path(pmodel).exists():
        print("  READY: set TTS_PROVIDER=piper, VOICE_PIPELINE=true and render.")
    else:
        print("  Next: record with tools/voice/RECORDING_CHECKLIST.md → "
              "voice dataset → Colab notebook → set PIPER_BIN / PIPER_MODEL.")
    return 0 if usable else 1
