"""Independent evidence -> script -> review -> render -> publish pipeline."""
import argparse
import json
import re
from dataclasses import replace
from pathlib import Path
from .research import ROOT, now, collect, select, evidence
from .llm import Writer
from .guards import validate, review, accepted
from .renderer import render
from .title_system import build_title_package
from .ledger import digest_file
from .media_check import check as check_media

SECTIONS = [('0:00–0:20','훅',34), ('0:20–0:50','맥락',51),
            ('0:50–1:45','핵심 1',93), ('1:45–2:40','핵심 2',93),
            ('2:40–3:30','핵심 3',85), ('3:30–4:20','시사점',85),
            ('4:20–5:00','요약 + 예고',69)]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def generate(writer, stories, out, existing=None):
    drafts, reports = [], []
    date = now().strftime('%Y년 %m월 %d일')
    if existing:
        for section in re.split(r'(?m)^## .*\n', existing)[1:]:
            drafts.append(re.sub(r'(?m)^\[화면 출처.*$', '', section).replace('[SHORTS_HOOK]', '').strip())
        if len(drafts) != 7:
            raise RuntimeError('Resume artifact has invalid sections')
    for index, (stamp, label, budget) in enumerate(SECTIONS):
        if existing:
            break
        source = evidence(stories[index-2]) if 2 <= index <= 4 else '\n'.join(
            f"{s['headline']}\n" + (drafts[j+2] if len(drafts) > j+2 else evidence(s)[:1600])
            for j,s in enumerate(stories))
        prompt = f'''오늘의엔터 정치 브리핑. 기준일 {date}, 한국시간. 자료에 나온 사건 발생일과
기사 발행일을 구분하고, 이전 날짜의 일을 오늘 발생한 것처럼 말하지 마라.
본문에 '오늘(22일)'이라고 있으면 사건일은 22일이다. RSS 발행일 23일로 대체 금지.
발생일이 불명확하면 '해당 보도에 따르면'이라고 표현하고 날짜를 추측하지 마라.
전체 구조: 훅→맥락→이슈 세 가지→시사점→요약과 예고.
지금은 '{label}' 본문만 공백 기준 {budget}어절(±5어절)로 작성.
40~60대가 이해할 수 있는 완결된 존댓말 한국어 문장(~합니다/~했습니다).
출력은 내레이션만, 제목/마크다운/메모 금지. 모든 구간에서 기준일을 반복하지 마라.
확인된 사실→왜 중요한가를 설명하라. 주장과 사실을 구분. 자료 속 반론을 반영하고,
쟁점에 반론이 없으면 이번에 확보한 자료에서 상대 입장을 확인하지 못했다고 밝혀라.
동일한 주장을 반론이라고 부르거나, 원문에 없는 가상의 반론을 덧붙이지 마라.
기관명은 원문 그대로. 유엔사는 유엔군사령부이며 유엔 사무국으로 바꾸지 마라.
미래 결과를 단정하지 말 것. 분량을 위해 반복하거나 사실을 보태지 말 것.
원문에 '최초'라고 확인되지 않으면 최초/처음이라고 확대 해석하지 마라.
정책의 세 가지 방향 등 항목을 요약할 때 원문의 항목과 정확히 대응시켜라.
훅이면 '{date}'를 반드시 포함하고 가장 중요한 이슈로 시작.
핵심 구간이면 첫 문장을 쇼츠로 추출해도 이해되도록 작성.
요약 구간이면 세 이슈를 하나씩 빠짐없이 요약하고 '내일은 후속 발표가 나왔는지 확인하겠습니다'처럼
확인 계획을 예고하되, 확정되지 않은 사건을 내일 발생한다고 약속하지 마라.
자료:\n{source}'''
        text = writer.ask(prompt, 3200)
        text = re.sub(r'^#+.*\n?', '', text, flags=re.M).strip().strip('`')
        drafts.append(text)
        out.with_suffix('.draft.md').write_text('\n\n'.join(drafts), encoding='utf-8')
        print(f'Section {label}: {len(text.split())} words', flush=True)
    # Repair only the section responsible for an out-of-band total. Always
    # retain original evidence and re-review the final assembled narration.
    for attempt in range(3):
        total = sum(len(d.split()) for d in drafts)
        if 460 <= total <= 560:
            break
        deltas = [len(d.split())-SECTIONS[i][2] for i,d in enumerate(drafts)]
        i = (max if total > 560 else min)(range(7), key=lambda n: deltas[n])
        target = max(25, len(drafts[i].split()) + 510-total)
        source = evidence(stories[i-2]) if 2 <= i <= 4 else '\n'.join(evidence(s)[:1600] for s in stories)
        revised = writer.ask(f'''기준일 {date}. 아래 '{SECTIONS[i][1]}' 대본의 분량만 조절하라.
현재 전체 {total}어절. 이 구간을 공백 기준 약 {target}어절로 {'줄여라' if total > 560 else '풀어 설명하라'}.
원문에 없는 사실/날짜/수치/전망을 추가하지 마라. 상대 입장과 주장 귀속 보존. 중복 반복 금지.
완결된 한국어 내레이션만 반환. 훅의 기준일은 보존.
근거:\n{source}\n기존 대본:\n{drafts[i]}''', 3200)
        candidate_total = total-len(drafts[i].split())+len(revised.split())
        if abs(candidate_total-510) < abs(total-510):
            drafts[i] = revised
    def assemble():
        parts = []
        for i, ((stamp,label,_), body) in enumerate(zip(SECTIONS,drafts)):
            if 2 <= i <= 4:
                body = '[SHORTS_HOOK] ' + body
                s = stories[i-2]['sources'][0]
                body += f"\n[화면 출처 텍스트: {s['name']} | {s['published'][:10]} | {s['url']}]"
            parts.append(f'## {stamp} | {label}\n{body}')
        return '\n\n'.join(parts)
    script = assemble()
    out.write_text(script, encoding='utf-8')
    validate(script, stories)
    for i, story in enumerate(stories):
        report = review(writer, drafts[i+2], evidence(story))
        save(out.with_suffix(f'.issue-{i+1}-initial.json'), report)
        if not accepted(report):
            drafts[i+2] = writer.ask(f'''기준일 {date}. 원문과 대조한 편집 검수에서 아래 오류가 발견됐다.
{json.dumps(report,ensure_ascii=False)}
지적을 전부 수정하라. 원문에 없는 확정적 인과관계·전망·반론을 없애고, 불확실한 사실은
누구의 추정인지 귀속해라. 기관명은 원문 그대로 쓰고 유엔사와 유엔 사무국을 혼동하지 마라.
기사 발행일과 사건 발생일을 구분. 예정인 연설을 이미 완료했다고 쓰지 마라.
약 {len(drafts[i+2].split())}어절의 내레이션 본문만 반환.
원문:\n{evidence(story)}\n대본:\n{drafts[i+2]}''', 3200)
            out.write_text(assemble(), encoding='utf-8')
            report = review(writer, drafts[i+2], evidence(story))
        reports.append(report)
        save(out.with_suffix('.quality.json'), reports)
        if not accepted(report):
            raise RuntimeError(f'Issue {i+1} failed 95-point review: {report}')
    indices = (0,1,5,6)
    frame = '\n'.join(f'[{SECTIONS[i][1]}]\n{drafts[i]}' for i in indices)
    frame_evidence = '\n'.join(evidence(s)[:2000] for s in stories)
    report = review(writer, frame, frame_evidence)
    save(out.with_suffix('.framing-initial.json'), report)
    if not accepted(report):
        fixed = writer.json(f'''기준일 {date}. 아래 검수 오류를 원문에 근거해서 모두 수정하라.
{json.dumps(report,ensure_ascii=False)}
기관명·사건 발생일 정확성, 주장 귀속, 아직 예정인 일의 시제를 확인.
훅에 기준일 유지. 각 구간 분량 유지. 요약에는 반드시 서로 다른 세 이슈 모두 포함.
JSON 형식: {{"hook":"본문","context":"본문","implications":"본문","outro":"본문"}}
원문:\n{frame_evidence}\n대본:\n{frame}''', 3200)
        for i,key in zip(indices,('hook','context','implications','outro')):
            if not isinstance(fixed.get(key),str) or not fixed[key].strip():
                raise RuntimeError('Invalid framing revision')
            drafts[i] = fixed[key].strip()
        out.write_text(assemble(), encoding='utf-8')
        frame = '\n'.join(f'[{SECTIONS[i][1]}]\n{drafts[i]}' for i in indices)
        report = review(writer, frame, frame_evidence)
    reports.append(report)
    save(out.with_suffix('.quality.json'), reports)
    if not accepted(report):
        raise RuntimeError(f'Framing failed 95-point review: {report}')
    script = assemble()
    out.write_text(script, encoding='utf-8')
    validate(script, stories)
    return script, reports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--publish', action='store_true')
    ap.add_argument('--output-dir', type=Path, default=ROOT/'longform_output')
    ap.add_argument('--resume-dir', type=Path)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    day = now().date().isoformat()
    if args.publish:
        from .ledger import Ledger, episode_key
        if Ledger().get(episode_key(day)):
            print('Daily episode already reserved or uploaded. Skip.', flush=True)
            return
    existing = None
    if args.resume_dir:
        source_path = args.resume_dir/'sources.json'
        draft_path = args.resume_dir/f'{day}.script.md'
        if not draft_path.exists():
            raise RuntimeError('Resume draft not from today')
        stories = json.loads(source_path.read_text(encoding='utf-8'))
        existing = draft_path.read_text(encoding='utf-8')
        # Explicit editorial errata apply only to this exact draft. They never
        # bypass regeneration of all quality reports or affect future scripts.
        errata_path = ROOT/'longform_system/editorial_corrections'/f'{day}.json'
        if errata_path.exists():
            errata = json.loads(errata_path.read_text(encoding='utf-8'))
            if errata['draft_sha256'] == digest_file(draft_path):
                existing = existing.replace('\u202f', ' ')
                for old, new in errata['replacements']:
                    if existing.count(old) != 1:
                        raise RuntimeError('Editorial correction target is not unique')
                    existing = existing.replace(old, new)
                save(args.output_dir/'editorial-corrections-applied.json', errata)
                print('Applied source-checked editorial corrections; full re-review still required', flush=True)
    else:
        stories = select(collect())
    save(args.output_dir/'sources.json', stories)
    writer = Writer()
    script_path = args.output_dir/f'{day}.script.md'
    script, reports = generate(writer, stories, script_path, existing)
    package = build_title_package({'theme':stories[0]['headline'], 'chapters':stories})
    title = writer.ask('아래 기사 근거로 오늘의엔터 정치 브리핑 제목 하나만 작성. '
                       '55자 이하, 쉬운 한글, 선정적 표현 금지. 핵심 기관·정책 명칭을 중간에서 자르지 마라. '
                       '5분 브리핑이라는 형식을 자연스럽게 표시해도 된다. 제목 이외 출력 금지.\n'
                       + evidence(stories[0]), 1200).strip().strip('"')
    if not 10 <= len(title) <= 55 or '\n' in title:
        raise RuntimeError('Generated title length/format invalid')
    package = replace(package, title=title)
    save(args.output_dir/'title-package.json', package.to_dict())
    title_review = review(writer, package.title, evidence(stories[0]), kind='title')
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
