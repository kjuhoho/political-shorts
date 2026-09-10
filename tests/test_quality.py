"""QUALITY CHECKER — 100-point score, band, publish gate."""
from pathlib import Path

from political_shorts.quality import check


def _script(**over):
    base = {
        "headline": "국회 예산안 본회의 통과",
        "source_text": "국회는 3일 본회의에서 내년도 예산안을 처리했다. 예산안은 찬성 210표로 가결됐다. "
                       "국민의힘은 합의 처리라고 밝혔고 더불어민주당은 독소조항이라고 반발했다.",
        "disclaimer": "공개 보도를 정리한 자동 제작물입니다.",
        "length_band": [50, 75],
        "est_seconds": 60,
        "factcheck": {"review_required": False},
        "sources": [{"name": "연합뉴스", "url": "u1", "lean": "wire"},
                    {"name": "한겨레", "url": "u2", "lean": "left"},
                    {"name": "동아일보", "url": "u3", "lean": "right"}],
        "segments": [
            {"role": "hook", "narration": "예산안이 3일 국회 본회의를 통과했습니다.",
             "caption": "예산안이 3일 국회 본회의를 통과했습니다."},
            {"role": "what", "narration": "찬성 210표로 가결됐습니다.",
             "caption": "찬성 210표로 가결됐습니다."},
            {"role": "factcheck", "caption": "팩트체크",
             "narration": "확인된 사실은 이겁니다.",
             "rows": [{"tag": "사실", "text": "국회 예산안 처리", "source": "연합뉴스, 한겨레"}]},
            {"role": "outro", "narration": "구독과 좋아요 부탁드립니다.",
             "caption": "구독과 좋아요 부탁드립니다."},
        ],
    }
    base.update(over)
    return base


def _meta(script, dur=60.0):
    tl = []
    t = 1.6
    for i, s in enumerate(script["segments"]):
        d = 3.5
        tl.append({"i": i, "start": round(t, 2), "end": round(t + d, 2), "dur": d,
                   "role": s["role"], "caption": s.get("caption", ""),
                   "audio_s": 0.0 if s["role"] == "factcheck" else 2.6,
                   "transition": "dissolve"})
        t += d
    return {"timeline": tl, "duration_s": dur, "sources": script["sources"]}


def test_clean_script_scores_well_and_is_publishable(tmp_path):
    sc = _script()
    qr = check(sc, _meta(sc), tmp_path / "x.mp4", type("C", (), {"ffmpeg_path": "ffmpeg"})())
    assert qr.score >= 80
    assert qr.band in ("PASS", "MINOR_REVISION")
    assert qr.publishable is True
    assert set(qr.categories) == {"content", "subtitle", "visual", "audio", "story", "source"}


def test_added_number_is_a_critical_content_ding(tmp_path):
    sc = _script()
    sc["segments"][1]["narration"] = "무려 999명이 반대했습니다."
    sc["segments"][1]["caption"] = "무려 999명이 반대했습니다."
    qr = check(sc, _meta(sc), tmp_path / "x.mp4", type("C", (), {"ffmpeg_path": "ffmpeg"})())
    assert any(i["code"] == "added-number" for i in qr.issues)
    assert qr.categories["content"][0] < 25


def test_caption_not_verbatim_is_flagged(tmp_path):
    sc = _script()
    sc["segments"][0]["caption"] = "예산안 통과"          # compressed, not the full line
    qr = check(sc, _meta(sc), tmp_path / "x.mp4", type("C", (), {"ffmpeg_path": "ffmpeg"})())
    assert any(i["code"] == "caption-not-verbatim" for i in qr.issues)


def test_fact_review_blocks_publish(tmp_path):
    sc = _script(factcheck={"review_required": True, "review_reason": "뇌물"})
    qr = check(sc, _meta(sc), tmp_path / "x.mp4", type("C", (), {"ffmpeg_path": "ffmpeg"})())
    assert qr.fact_check_ok is False
    assert qr.publishable is False
