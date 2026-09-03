"""Package local config + credentials into GitHub Actions secrets.

    python scripts/pack_secrets.py            # print the 3 `gh secret set` commands
    python scripts/pack_secrets.py --write    # also write them (needs the `gh` CLI, authed)

The daily GitHub Actions workflow (.github/workflows/daily-short.yml) rebuilds
`.env` and `secrets/*.json` from these, so the pipeline can collect / build /
publish entirely on GitHub's servers — no PC needed.
"""
import base64
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WANT = {
    "PS_ENV_B64": ROOT / ".env",
    "PS_YT_CLIENT_B64": ROOT / "secrets" / "client_secret_youtube.json",
    "PS_YT_TOKEN_B64": ROOT / "secrets" / "token_youtube.json",
}


def main() -> int:
    write = "--write" in sys.argv          # push via the `gh` CLI
    missing = [str(p) for p in WANT.values() if not p.exists()]
    if missing:
        print("missing files:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2

    out_dir = ROOT / "secrets" / "gha"     # gitignored (secrets/ is ignored)
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, path in WANT.items():
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        (out_dir / f"{name}.txt").write_text(b64, encoding="ascii")
        print(f"{name:18} <- {path.name:28} ({len(b64):>5} chars)  ->  secrets\\gha\\{name}.txt")
        if write:
            r = subprocess.run(["gh", "secret", "set", name, "--body", b64],
                               capture_output=True, text=True)
            print("   gh: " + (r.stdout or r.stderr).strip())

    if not write:
        print()
        print("Open each secrets\\gha\\*.txt, copy ALL of it, and paste as the value of a")
        print("repo secret of the same name on github.com  (Settings > Secrets and")
        print("variables > Actions > New repository secret).")
        print("DELETE the secrets\\gha\\ folder afterwards — it holds your live token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
