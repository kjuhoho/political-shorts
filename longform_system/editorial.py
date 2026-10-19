"""Explanation-first editorial contract, within existing generation/review calls."""
VERSION = 1
GENERATION_ORDER = (2,3,4,0,1,5,6)

COMMON = '''편집 원칙: 발표문 낭독이 아니라 시청자 질문에 답하는 설명 영상이다.
새로 이해할 차이·조건·작동 원리 하나를 남겨라. '중요합니다/주목됩니다/파장이 큽니다'
만으로 중요성을 대신하지 마라. 관료적 표현은 즉시 쉬운 말로 풀고 한 문장에 한 생각만 담아라.
근거에 있는 구체적 사례·전후 차이·절차 중 하나로 설명하되 없는 예시/인과/생활 영향은 만들지 마라.
질문을 던지면 같은 이슈 안에서 답하라. 결론을 숨겨 끝까지 보라고 하거나 공포·진영 갈등으로 유인하지 마라.
관계없는 세 이슈를 같은 원인으로 묶지 마라. 같은 발표를 세 번 요약하지 마라.'''


def direction(index):
    if 2 <= index <= 4:
        return COMMON+'''\n이슈 구성: 시청자의 구체적인 물음 또는 겉보기와 실제의 차이 → 확인된 변화 →
왜/어떻게 그런지 근거로 설명 → 그 질문의 답과 아직 모르는 점.
핵심 설명 앞에는 자연스럽게 '쉽게 말하면', '차이는', '핵심은' 같은 전환을 사용할 수 있다.
첫 문장은 발표 기관 소개보다 핵심 물음/차이를 담되 따로 추출해도 이해되어야 한다.
마지막에는 앞의 물음에 대한 답을 실제로 말하라. 추상적인 의미 부여로 끝내지 마라.'''
    if index == 0:
        return COMMON+'''\n이미 작성된 핵심 이슈가 답할 수 있는 구체적 질문/차이로 시작한다.
날짜는 첫 문장을 밀어내지 않도록 뒤에 자연스럽게 포함한다. '가장 큰 파장' 같은 빈 과장 금지.
세 기사의 제목을 읽는 대신 이번 영상에서 무엇을 구별할 수 있는지 알려라.'''
    if index == 1:
        return COMMON+'\n훅을 반복하지 말고 핵심 설명에 필요한 배경/용어를 먼저 풀어라.'
    if index == 5:
        return COMMON+'\n이미 설명한 사건을 반복하지 말고 자료에서 확인되는 조건/미정인 절차/다음 판단 기준을 설명하라.'
    return COMMON+'''\n기사 제목 세 줄이 아니라 각 질문의 답을 하나씩 요약하라.
예고는 자료로 확인되는 구체적 후속 확인 항목을 말하라. 일정이 없으면 내일 결과가 나온다고 약속하지 마라.'''


def review_contract(kind):
    scope = ('도입의 질문과 요약의 답이 대응하는지, 맥락은 용어/배경을 풀고 시사점은 판단 조건을 주는지'
             if kind == 'framing' else '이슈 안에서 물음/차이를 제시하고 원리·조건·비교를 설명해 답하는지')
    return f'''\n설명력 검수 v{VERSION}: {scope} 평가한다.
표면적인 물음표, '쉽게 말하면', '중요합니다'만 있으면 통과시키지 마라.
추가 JSON editorial: {{"question_quote":"물음/대조를 담은 정확한 대본 인용",
"explanation_quote":"원리/차이/조건을 푼 정확한 대본 인용",
"payoff_quote":"답/새로 이해한 점의 정확한 대본 인용",
"source_quote":"설명을 뒷받침하는 정확한 원문 인용",
"question_answered":true,"adds_understanding":true,"non_repetitive":true,
"reason":"구체적 판단 이유"}}.
인용은 각각 4자 이상 연속 복사. 해당 요소가 없으면 그 인용만 빈 문자열로 하고 해당 판단을 false로 한다.
설명 부족을 사실 오류 findings에 억지로 넣지 마라. 원문에 없는 설명/비유를 만들라고 권하지 마라.'''


def audit(report,text,sources):
    """Validate evidence fields; absence of learning value is a real hold, not a parser failure."""
    item = report.get('editorial')
    if not isinstance(item,dict):
        return ['editorial 객체가 없습니다.']
    errors=[]
    norm=lambda s:' '.join(s.split())
    for field in ('question_answered','adds_understanding','non_repetitive'):
        if type(item.get(field)) is not bool:
            errors.append('editorial.'+field+' 불리언이 없습니다.')
    for field in ('question_quote','explanation_quote','payoff_quote','source_quote'):
        value=item.get(field)
        original=sources if field=='source_quote' else text
        if not isinstance(value,str) or (value and (len(value.strip())<4 or norm(value) not in norm(original))):
            errors.append('editorial.'+field+'는 정확한 연속 인용이어야 합니다.')
    if not isinstance(item.get('reason'),str) or not item['reason'].strip():
        errors.append('editorial.reason이 없습니다.')
    if not errors:
        report['editorial_ok'] = (all(item[k] for k in ('question_answered','adds_understanding','non_repetitive'))
                                 and all(item[k].strip() for k in ('question_quote','explanation_quote','payoff_quote','source_quote'))
                                 and item['explanation_quote'] != item['question_quote'])
    return errors
