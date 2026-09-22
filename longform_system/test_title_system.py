from longform_system.title_system import build_title_package


def test_title_package_is_neutral_and_has_sources():
    plan = {
        "theme": "대통령실 인사와 안보 현안",
        "chapters": [{
            "headline": "대통령비서실장 사의 표명과 후속 인선",
            "sources": [{"name": "공식 발표", "url": "https://example.com/source"}],
        }],
    }
    package = build_title_package(plan)
    assert len(package.title) <= 55
    assert "충격" not in package.title
    assert "https://example.com/source" in package.description
    assert package.candidates[0].score >= package.candidates[-1].score
