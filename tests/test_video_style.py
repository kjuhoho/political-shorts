"""Central design system + top-bar phase logic."""
from political_shorts import video
from political_shorts.hook import make_title, detect_entities, detect_frame


def test_style_json_loads_with_the_keys_the_renderer_needs():
    st = video._style()
    assert st, "config/video_style.json should load"
    assert "subtitle" in st and "topbar" in st and "title" in st
    assert st["subtitle"]["max_lines"] >= 2
    assert isinstance(st["topbar"]["section_names"], dict)
    assert st["topbar"]["section_names"].get("factcheck")


def test_title_lines_are_not_cut_mid_word():
    h = "이재명 대통령 임기 중 연임 개헌 논의 꺼내...정치권 논란"
    lines = make_title(h, detect_entities(h), detect_frame(h))
    for ln in lines:
        assert not ln.endswith(" 연")           # the old mid-word clip
        assert ln == ln.strip()
        assert len(ln) <= 18
