"""Centralised logging: console + rotating file in ``logs/``."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import settings
from .console import enable_utf8

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger("political_shorts")
    if _CONFIGURED:
        return root

    enable_utf8()

    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    logfile = settings.log_dir / "political_shorts.log"
    fileh = RotatingFileHandler(
        logfile, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    fileh.setFormatter(fmt)
    root.addHandler(fileh)

    root.propagate = False
    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"political_shorts.{name}")
