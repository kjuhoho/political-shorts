"""Independent evidence -> script -> review -> render -> publish pipeline."""
import argparse
import json
import re
from pathlib import Path
from .research import ROOT, now, collect, select, evidence
from .llm import Writer
from .guards import validate, review, accepted
from .renderer import render
from .title_system import build_title_package
from .ledger import digest_file
from .media_check import check as check_media

SECTIONS = [('0:00–0:20','훅',50), ('0:20–0:50','맥락',80),
            ('0:50–1:45','핵심 1',150), ('1:45–2:40','핵심 2',150),
            ('2:40–3:30','핵심 3',140), ('3:30–4:20','시사점',120),
            ('4:20–5:00','요약 + 예고',110)]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def generate(writer, stories, out):
    drafts, reports = [], []
    date = now().strftime('%Y년 %m월 %d일')
    for index, (stamp, label, budget) in enumerate(SECTIONS):
        source = evidence(stories[index-2]) if 2 <= index <= 4 else '\n'.join(
            f"{s['headline']}\n" + (drafts[j+2] if len(drafts) > j+2 else evidence(s)[:1600])
            for j,s in enumerate(stories))
        prompt = f'''오늘의엔터 정치 브리핑. 기준일 {date}, 한국시간. 자료에 나온 사건 발생일과
기사 발행일을 구분하고, 이전 날짜의 일을 오늘 발생한 것처럼 말하지 마라.
전체 구조: 훅→맥락→이슈 세 가지→시사점→요약과 예고.
지금은 '{label}' 본문만 공백 기준 {budget}어절(±5어절)로 작성.
40~60대가 이해할 수 있는 완결된 한국어 문장. 출력은 내레이션만, 제목/마크다운/메모 금지.
확인된 사실→왜 중요한가를 설명하라. 주장과 사실을 구분. 자료 속 반론을 반영하고,
반론이 없으면 이번에 확보한 자료에서 상대 입장을 확인하지 못했다고 밝혀라.
미래 결과를 단정하지 말 것. 분량을 위해 반복하거나 사실을 보태지 말 것.
훅이면 '{date}'를 반드시 포함하고 가장 중요한 이슈로 시작.
핵심 구간이면 첫 문장을 쇼츠로 추출해도 이해되도록 작성.
요약 구간이면 세 가지 요약과 '내일은 후속 발표가 나왔는지 확인하겠습니다'처럼
확인 계획을 예고하되, 확정되지 않은 사건을 내일 발생한다고 약속하지 마라.
자료:\n{source}'''
        text = writer.ask(prompt, 3200)
        text = re.sub(r'^#+.*\n?', '', text, flags=re.M).strip().strip('`')
        drafts.append(text)
        out.with_suffix('.draft.md').write_text('\n\n'.join(drafts), encoding='utf-8')
        print(f'Section {label}: {len(text.split())} words', flush=True)
    parts = []
    for i, ((stamp,label,_), body) in enumerate(zip(SECTIONS,drafts)):
        if 2 <= i <= 4:
            body = '[SHORTS_HOOK] ' + body
            s = stories[i-2]['sources'][0]
            body += f"\n[화면 출처 텍스트: {s['name']} | {s['published'][:10]} | {s['url']}]"
        parts.append(f'## {stamp} | {label}\n{body}')
    script = '\n\n'.join(parts)
    out.write_text(script, encoding='utf-8')
    validate(script, stories)
    for i, story in enumerate(stories):
        report = review(writer, drafts[i+2], evidence(story))
        reports.append(report)
        save(out.with_suffix('.quality.json'), reports)
        if not accepted(report):
            raise RuntimeError(f'Issue {i+1} failed 95-point review: {report}')
    frame = '\n'.join(drafts[i] for i in (0,1,5,6))
    report = review(writer, frame, '\n'.join(evidence(s)[:3000] for s in stories))
    reports.append(report)
    save(out.with_suffix('.quality.json'), reports)
    if not accepted(report):
        raise RuntimeError(f'Framing failed 95-point review: {report}')
    return script, reports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--publish', action='store_true')
    ap.add_argument('--output-dir', type=Path, default=ROOT/'longform_output')
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    day = now().date().isoformat()
    if args.publish:
        from .ledger import Ledger, episode_key
        if Ledger().get(episode_key(day)):
            print('Daily episode already reserved or uploaded. Skip.', flush=True)
            return
    stories = select(collect())
    save(args.output_dir/'sources.json', stories)
    writer = Writer()
    script_path = args.output_dir/f'{day}.script.md'
    script, reports = generate(writer, stories, script_path)
    package = build_title_package({'theme':stories[0]['headline'], 'chapters':stories})
    title_review = review(writer, '제목만 검수 (본문 배경설명 요구 금지): '+package.title, evidence(stories[0]))
    save(args.output_dir/'title-review.json', title_review)
    if not accepted(title_review):
        raise RuntimeError('Title failed review')
    video = args.output_dir/f'{day}.mp4'
    manifest = render(script_path, video, ROOT/'assets/fonts/DoHyeon-Regular.ttf', 'ko-KR-SunHiNeural')
    media_report = check_media(video)
    save(video.with_suffix('.media.json'), media_report)
    if not 240 <= manifest['duration_s'] <= 360:
        raise RuntimeError(f"Duration outside 4–6 minutes: {manifest['duration_s']}")
    meta = dict(title=package.title, description=f'기준일: {day} (한국시간)\n'+package.description,
                tags=package.tags, privacy_status='public', category_id='25', date=day,
                sources=stories, render=manifest, quality=reports, title_review=title_review,
                video_sha256=digest_file(video), media_check=media_report,
                usage={'calls':writer.calls,'tokens':writer.tokens})
    save(video.with_suffix('.meta.json'), meta)
    if args.publish:
        from .youtube_upload import upload
        result = upload(video, meta)
        save(args.output_dir/'published.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
