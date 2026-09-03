"""political-shorts: a personal, offline-first pipeline that turns domestic
Korean political news into short vertical videos.

Design principles
-----------------
* Offline by default. No API key is required to collect news, build a script,
  run the safety checks, or render a video.
* Publishing is opt-in. ``ENABLE_PUBLISH`` gates every network write; until it
  is true, publishers only log what they would do.
* Neutral by construction. The script generator emits attributed facts and
  clearly labelled interpretation, and the safety layer blocks anything that
  looks like an unsourced claim, a one-sided framing, or harmful language.
"""

__version__ = "1.0.0"
