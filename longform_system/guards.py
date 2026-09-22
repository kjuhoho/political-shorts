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
검증은 제공 원문과 대본을 문장 단위로 대조한다. 모델 사전학습 기억으로 현직 인물이나
원문의 최신 정보를 뒤집지 마라. 외부 검증을 하지 않았다면 했다고 가정하지 마라.
특히 '유엔사'를 '유엔 사무국'으로 바꾸면 기관 오류다. 한국군 작전을 '한미 연합군 작전'으로
바꾸면 주체 오류다. 예정된 연설을 완료된 연설로 바꾸면 날짜/시제 오류다.
원문에 있는 불확실한 추정을 확정적 사건으로 바꾸면 사실 오류다. 보도일을 사건일로 쓰지 마라.
평가는 원문 대비 충실성이다. 외부 진실 여부는 이 호출에서 확인할 수 없다.
현직 인물이 학습 기억과 다르다는 이유로 원문을 부정하지 마라.
문제가 있으면 findings에 script_quote(대본의 정확한 연속 인용),
source_quote(관련 원문의 정확한 연속 인용), reason(차이)을 반드시 기록하라.
인용은 복사하고 의역하지 마라. 오류가 없으면 findings는 빈 배열이다.
날짜는 발행 시각보다 본문의 '(21일)', '(22일)' 등 명시적 사건일을 우선한다.
반환: {"score":정수,"facts_ok":불리언,"dates_ok":불리언,"balance_ok":불리언,
"safety_ok":불리언,"issues":["문장과 구체적 사유"],
"findings":[{"script_quote":"정확한 대본 인용","source_quote":"정확한 원문 인용","reason":"차이"}]}'''


def grounded(report, text, sources):
    """Unanchored criticism must never be used to rewrite a factual draft."""
    findings = report.get('findings')
    if not isinstance(findings, list):
        return False
    if not accepted(report) and not findings:
        return False
    normalize = lambda value: ' '.join(value.split())
    for item in findings:
        if not isinstance(item, dict):
            return False
        for key, original in (('script_quote', text), ('source_quote', sources)):
            quote = item.get(key)
            if not isinstance(quote, str) or len(quote.strip()) < 4 or normalize(quote) not in normalize(original):
                return False
        if not isinstance(item.get('reason'), str) or not item['reason'].strip():
            return False
    return True


def accepted(report):
    return (type(report.get('score')) is int and 95 <= report['score'] <= 100
            and all(report.get(k) is True for k in ('facts_ok','dates_ok','balance_ok','safety_ok'))
            and isinstance(report.get('issues'), list))


def publication_reviews_ok(meta):
    reports = meta.get('quality')
    return (isinstance(reports, list) and len(reports) == 4
            and all(isinstance(r, dict) and accepted(r) for r in reports)
            and isinstance(meta.get('title_review'), dict)
            and accepted(meta['title_review']))


def review(writer, text, sources):
    prompt = (f'기준일: {now():%Y년 %m월 %d일} (한국시간)\n{RUBRIC}\n'
              f'검증 자료:\n{sources}\n검수 대상:\n{text}')
    for attempt in range(2):
        report = writer.json(prompt, 2000)
        if grounded(report, text, sources):
            return report
        prompt += '\n이전 응답은 원문/대본에 실제 존재하는 인용 근거가 없어 무효였다. 정확한 인용을 포함해 새로 검수하라.'
    raise RuntimeError('Reviewer did not ground its findings; refusing unsupported repair or publication')


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
    if not 460 <= words <= 560:
        raise RuntimeError(f'Script has {words} words, needs 460–560 for natural 5-minute narration')
    source_text = ' '.join(s['body'] for story in stories for s in story['sources']) + now().strftime('%Y년 %m월 %d일')
    for year in re.findall(r'\b(20\d{2})년', body):
        if year not in source_text:
            raise RuntimeError(f'Unsupported year: {year}')
    today = now()
    if not re.search(fr'{today.year}년\s*0?{today.month}월\s*0?{today.day}일', body):
        raise RuntimeError('Missing explicit briefing date')
    return words
