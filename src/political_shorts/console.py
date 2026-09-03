"""Force UTF-8 on stdout/stderr so Korean + typographic punctuation print on
the default Windows console (cp949) without UnicodeEncodeError."""
from __future__ import annotations

import sys


def enable_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
