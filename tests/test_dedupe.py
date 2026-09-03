from political_shorts.dedupe import SIM_THRESHOLD, _cluster_rows, similarity


def make(i, title, lean="wire", weight=0.9):
    """A dict is enough: the module only does row["key"] access."""
    return {"id": i, "title": title, "source_lean": lean, "source_weight": weight}


def test_similar_headlines_cluster_together():
    a = make(1, "여야, 국회 본회의서 예산안 처리 두고 충돌", "wire", 1.0)
    b = make(2, "[속보] 국회 본회의 예산안 처리 놓고 여야 정면 충돌", "right", 0.7)
    c = make(3, "손흥민 2골…토트넘 완승", "wire", 0.9)
    assert similarity(a, b) >= SIM_THRESHOLD
    assert similarity(a, c) < SIM_THRESHOLD
    clusters = _cluster_rows([a, b, c])
    sizes = sorted(len(cl.article_ids) for cl in clusters)
    assert sizes == [1, 2]


def test_distinct_stories_stay_separate():
    a = make(1, "대통령, 개각 단행…국무총리 후보자 지명")
    b = make(2, "국회 법사위, 특검법 상정 불발")
    clusters = _cluster_rows([a, b])
    assert len(clusters) == 2


def test_single_article_forms_cluster_of_one():
    a = make(1, "정개특위, 선거구 획정안 논의 착수")
    clusters = _cluster_rows([a])
    assert len(clusters) == 1 and clusters[0].size == 1


def test_attack_headlines_are_deprioritised():
    from political_shorts.dedupe import _is_attack_headline as a
    assert a('한동훈, 김승원에 "해명 않고 도망가...청문회 아닌 특검 받아야"')
    assert a('정점식, 조국 "공소취소 낭패" 주장에 "李대통령 레임덕 본격화"')
    assert a('한동훈 "이재명 정부는 무능"...국정운영 직격')
    assert not a('국회 신속처리안건 심사기간 330일에서 90일로 단축')
    assert not a('법사위, 이태원참사 특별법 개정안 의결')
    assert not a('용혜인, 장관 되면서 비례 의원직 유지 논란...여야 반발')
