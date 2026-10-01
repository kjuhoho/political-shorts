"""Live audit 2026-09-30: the National Assembly photo opened four of five videos, Seoul City Hall three,
the same road clip three. What a video used goes to the back of the queue for the next ones."""
import dataclasses
import time

from political_shorts import asset_memory
from political_shorts.config import settings


def _cfg(tmp_path):
    return dataclasses.replace(settings, data_dir=tmp_path)


POOL = ["대한민국 국회의사당", "광화문광장", "서울특별시청", "숭례문"]


def test_what_was_used_goes_last_and_the_rest_keep_their_order(tmp_path):
    cfg = _cfg(tmp_path)
    asset_memory.remember(cfg, ["대한민국 국회의사당", "서울특별시청"])
    assert asset_memory.freshest_first(POOL, cfg) == ["광화문광장", "숭례문", "대한민국 국회의사당", "서울특별시청"]


def test_an_old_use_stops_counting(tmp_path):
    cfg = _cfg(tmp_path)
    now = time.time()
    asset_memory.remember(cfg, ["대한민국 국회의사당"], now=now - 10 * 86400)
    assert asset_memory.recent(cfg, now=now) == set()
    assert asset_memory.freshest_first(POOL, cfg, now=now) == POOL


def test_nothing_is_ever_blocked_only_reordered(tmp_path):
    cfg = _cfg(tmp_path)
    asset_memory.remember(cfg, POOL)
    assert sorted(asset_memory.freshest_first(POOL, cfg)) == sorted(POOL)


def test_very_old_entries_are_forgotten_so_the_file_cannot_grow_forever(tmp_path):
    cfg = _cfg(tmp_path)
    now = time.time()
    asset_memory.remember(cfg, ["옛날사진"], now=now - 30 * 86400)
    asset_memory.remember(cfg, ["새사진"], now=now)
    import json
    kept = json.loads((tmp_path / asset_memory.FILE).read_text(encoding="utf-8"))
    assert list(kept) == ["새사진"]


def test_keys_come_from_assets_and_from_the_script_dicts():
    class _A:
        query, source_url, title = "광화문광장", "https://x/1", "Gwanghwamun"

    assert asset_memory.keys_of([_A()]) == ["광화문광장"]
    assert asset_memory.keys_of([{"source_url": "https://pexels/3", "title": "street"}]) == ["https://pexels/3"]
    assert asset_memory.keys_of([{}]) == []
