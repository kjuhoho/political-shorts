"""LAYOUT ENGINE — scene_type -> one of six layouts, subtitle drawn the same."""
from PIL import Image, ImageDraw

from political_shorts import layout
from political_shorts.config import settings


def test_layout_id_mapping():
    assert layout.layout_id("PERSON") == "02"
    assert layout.layout_id("QUOTE") == "03"
    assert layout.layout_id("NUMBER") == "04"
    assert layout.layout_id("COMPARISON") == "05"
    assert layout.layout_id("CONCLUSION") == "06"
    assert layout.layout_id("HOOK") == "01"
    assert layout.layout_id("") == "01"


def _draw(seg):
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    layout.render_caption(d, seg, settings, 1080, 1920, (245, 202, 66))
    return img


def test_every_layout_renders_without_error():
    for st, cap in [
        ("HOOK", "김승원, 취임 두 달 만에 물러났습니다."),
        ("PERSON", "이재명 대통령이 개헌 논의를 꺼냈습니다."),
        ("QUOTE", '장동혁 원내대표는 "강행 처리는 없다"고 말했습니다.'),
        ("NUMBER", "예산안은 찬성 210표로 통과됐습니다."),
        ("COMPARISON", "국민의힘과 민주당이 정반대로 말합니다."),
        ("ISSUE", "법이 바뀌면 국민 생활에 영향을 줍니다."),
        ("CONCLUSION", "구독과 좋아요 눌러주시면 큰 힘이 됩니다."),
    ]:
        img = _draw({"scene_type": st, "caption": cap, "narration": cap})
        assert img.getbbox() is not None                # something was drawn


def test_empty_caption_is_a_noop():
    img = _draw({"scene_type": "HOOK", "caption": "", "narration": ""})
    assert img.getbbox() is None
