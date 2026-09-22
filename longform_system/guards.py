"""Independent adaptation of the shorts editor's 95-point rubric."""
import re
from .research import blocked, now

# Same exclusions as the existing channel safety policy, kept independent of
# its imports/provider configuration. Quoting these in a briefing also holds it.
HARMFUL = ('빨갱이','수구꼴통','토착왜구','친일파 새끼','국개','쓰레기 정당',
           '죽여','때려죽','몰살','처단하자','테러하','화형','목매달','쳐죽',
           '정신병자','틀딱','급식충','맘충','홍어','전라디언','착짱죽짱')

RUBRIC = '''100점에서 감점. 주제 일치, 쉬운 배경 설명, 완결된 문장, 용어 풀이,
구체적인 후속 확인 사항, 자연스러운 한국어, 발언 귀속 정확성, 원인과 반응 구분,
자료에 있는 상대 입장 포함, 날짜 정확성을 평가한다. 명백한 오류는 점수와 관계없이 차단.
원문에 없는 인물·수치·날짜·원인·혐의 추가, 혐오/비하, 근거 없는 범죄 단정은 차단한다.
자료에 반대 입장이 없으면 없다고 명시하는 것은 허용한다. 없는 반론을 만들지 않는다.
기사 문장을 그대로 이어붙이거나 반복해서 분량을 채우면 감점한다.
반환: {"score":정수,"facts_ok":불리언,"dates_ok":불리언,"balance_ok":불리언,
"safety_ok":불리언,"issues":["문장과 구체적 사유"]}'''


def accepted(report):
    return (type(report.get('score')) is int and 95 <= report['score'] <= 100
            and all(report.get(k) is True for k in ('facts_ok','dates_ok','balance_ok','safety_ok'))
            and isinstance(report.get('issues'), list))


def review(writer, text, sources):
    return writer.json(f'기준일: {now():%Y년 %m월 %d일} (한국시간)\n{RUBRIC}\n'
                       f'검증 자료:\n{sources}\n검수 대상:\n{text}', 950)


def validate(script, stories):
    if blocked(script):
        raise RuntimeError('Blocked topic in script')
    if any(term in script for term in HARMFUL):
        raise RuntimeError('Harmful wording in script')
    headings = re.findall(r'^## .*\| (.+)$', script, re.M)
    if headings != ['훅','맥락','핵심 1','핵심 2','핵심 3','시사점','요약 + 예고']:
        raise RuntimeError('Invalid section order')
    if script.count('[SHORTS_HOOK]') != 3:
        raise RuntimeError('Missing extraction hooks')
    for story in stories:
        if story['sources'][0]['url'] not in script:
            raise RuntimeError('Missing source disclosure')
    body = re.sub(r'^##.*$|^\[화면 출처.*$', '', script, flags=re.M).replace('[SHORTS_HOOK]', '')
    words = len(body.split())
    if not 750 <= words <= 850:
        raise RuntimeError(f'Script has {words} words, needs 750–850')
    source_text = ' '.join(s['body'] for story in stories for s in story['sources']) + now().strftime('%Y년 %m월 %d일')
    for year in re.findall(r'\b(20\d{2})년', body):
        if year not in source_text:
            raise RuntimeError(f'Unsupported year: {year}')
    today = now()
    if not re.search(fr'{today.year}년\s*0?{today.month}월\s*0?{today.day}일', body):
        raise RuntimeError('Missing explicit briefing date')
    return words
