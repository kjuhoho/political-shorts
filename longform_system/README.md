# 오늘의엔터 롱폼 시스템

## Fish Audio opt-in

Connection code is installed but the default remains Edge TTS. No Fish API
requests are made until explicitly configured. Create your own voice model in
Fish Audio first; never put the API key or voice recordings in this repository.

GitHub Actions secrets:
- `LONGFORM_FISH_API_KEY`: Fish API key (not a Groq/OpenAI key)
- `LONGFORM_FISH_VOICE_ID`: your own saved voice model/reference ID

GitHub Actions variables, set only after reviewing Fish's current usage terms:
- `LONGFORM_TTS_PROVIDER=fish`
- `LONGFORM_FISH_USE_ACK=true`: acknowledgement of voice rights, provider data
  retention/model-improvement use and applicable commercial usage conditions

Only `s2.1-pro-free` is allowed. No paid fallback, automatic retry, or voice
substitution. Free availability and commercial rights must be rechecked before
activation: https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits

Fish audio is synthesized once per scene, then reused for timing and encoding.
Matching completed local files are hash-checked and reused in the same working
directory. Cache is not yet restored between separate GitHub runs. Ambiguous
requests stop rather than silently spending again. Per video: at most 60 scenes
and 12,000 UTF-8 text bytes. Unit tests use mocked HTTP; real voice quality and
account access remain unverified until credentials and user consent are ready.

## Visual edition (2026-10-01)

The independent renderer now creates a deterministic per-sentence storyboard:
three-issue overview, literal short source excerpts, contextual number emphasis,
editorial topic illustrations, and licensed still/video inserts. Artwork moves
slightly while captions and source/date labels remain stationary. No additional
LLM or image-generation requests are used. `sources.json` beside the script is
required. A `.visual-plan.json` records assignments; `qa/` contains a decoded
midpoint frame from every encoded scene, not merely the source drawings.

Article excerpts are **redrawn source cards**, not screenshots or original video
footage. Article publication dates are labelled as such. Numbers retain their
full narration, including denials and forecasts; no invented chart comparisons,
geographic movements, actor portraits or event reconstructions are generated.

`media/library.json` contains visually inspected, hash-pinned reusable material.
The initial library contains one National Assembly exterior, photographed
2007-12-12, attributed to 대한민국 국회 under KOGL Type 1. It is used only for
narrow, evidence-backed Korean National Assembly anchors, never as a current
event photo. Images are bundled once, eliminating recurring search/download
costs. Library coverage is intentionally small: most stories use graphics.
This is NOT unrestricted automatic news-video collection.

Optional episode `media-registry.json` accepts a list of records with `reviewed`,
`license`, `license_url`, `credit`, `source_url`, `asset_page`, `sha256`, `path`,
`anchor`, and `caption`. Files must be inside the registry folder, <=50 MB,
JPEG/PNG/MP4, with exact hashes and explicit editorial review. `source_url`
must match the issue's primary evidence; `anchor` must match that narration.
Captions should include capture date and historical/illustrative status. Rights
and event correspondence require actual review, not an automatically set flag.
Allowed licences: CC0, CC-BY-4.0, KOGL-1, public-domain, own-work. Used media
credits, source pages, licence links and modification notices enter the YouTube
description. Video source audio is never included. Missing media uses graphics;
invalid supplied media fails closed. Do not mislabel all Wikimedia content CC0.

Offline preview without news/LLM/TTS calls:

```powershell
python -m longform_system.visual_preview --bundle <existing-artifact-folder> --output <preview-folder> --ffmpeg <ffmpeg-executable> --all-scenes
```

This reuses the saved MP4's narration and render manifest. It is prominently
marked historical/design-only and must not be published as today's briefing.
Fish Audio remains opt-in and is not activated by this visual upgrade.

## Publication contract

### Overseas evidence and media

`international.py` now supplements the selected Korean stories with dated full
text from BBC World, UN News, UK FCDO and the UK Parliament Lords Library feeds.
Only seven-day material within two days of the primary article, matching at
least three bilingual concepts (including an entity and a concrete topic), is
eligible for cross-checking. At most one foreign press item and one official
item per issue enter the source package. Government/institutional positions are
labelled, not treated as independent verification. Keyword matching is only
candidate selection; the existing 95-point reviewer compares event identity,
attribution, contradictions and translation of uncertainty/negation. No extra
LLM or translation call is added. Evidence input is capped at 4,000 characters
per issue, with balanced slots for up to four sources; framing uses smaller
balanced budgets. Shared shorts source configuration is unchanged.

Google News RSS English queries also collect a small discovery list. Those
aggregator links/snippets are **not** evidence and never grant image reuse rights;
unresolved originals are not quoted or used to generate claims. This is a
best-effort public RSS path, not Google Custom Search API or a guaranteed search
service. No API key, paid search or browser scraping is used.

The media collector now inspects the domestic primary and one English source
(official preferred), keeping its existing 18-request/50-MB budget. English
captions may match Korean narration through the explicit bilingual dictionary.
All prior per-file licence, exact original URL, date and integrity gates remain.
For cross-check candidate sources, automatic visual use additionally requires
the existing reviewer to return `source_checks` identifying the same event with
literal quotes from both originals. Missing/invalid checks hold overseas media;
they do not trigger another API call. This remains metadata plus text review,
not face recognition or independent proof that a photo shows the event.

The overseas text stage has its own bounded Fetcher (18 requests / 50 MB /
120-second cooperative deadline) and same-day input cache. At most two redirects
are permitted only within each configured publisher's explicit host list; media
downloads still do not follow redirects. `international-research.json` records
feed failures, Google discovery, candidate inclusion and network usage.

Live smoke check: BBC (32 entries), UN News (30), UK FCDO (20), and Google
discovery were accessible. After implementing allowlisted redirects, a targeted
Lords feed recheck returned 10 entries in three requests. No overseas sources were appended
to the historical September 23 sample; this was not reported as corroboration.

### Automatic event media (2026-10-01)

After script/title review and before TTS, `media_collect.py` inspects the three
primary article pages' JSON-LD/OG candidates and searches Wikimedia Commons
(up to three files per issue, imageinfo + per-file extended metadata). It uses
no keys, LLMs, paid image generation or YouTube scraping. The automatic gate
requires a **per-asset** CC0/CC-BY-4.0/KOGL-1 licence URL, named creator, exact
article association, explicit creation/capture date within the two days before
publication, and at least two non-generic caption/title/narration terms. Upload
dates are never used as capture dates. Archival/synthetic captions are held.

This is conservative metadata matching, **not visual face recognition or proof
that a photo depicts the alleged act**. Captions say "보도 연결 자료" and disclose
that the date is metadata-based. Files without reliable metadata remain held;
an episode may legitimately receive zero event media. Name-only stock portraits,
OG-only images and site-wide copyright footers never authorize automatic reuse.
Currently only HTTPS same-origin media and upload.wikimedia.org originals are
downloaded. Unmapped CDNs, redirects, paywalls, YouTube players, playlists,
unknown licences, CC-BY-SA and non-commercial/no-derivatives licences are held.

Budget: <=18 HTTP requests, <=50 MB response data, <=12 MB per media download,
120-second cooperative deadline, <=2 accepted assets per issue. No HTTP retries.
Public DNS/IP checks and no redirects exclude internal URLs. Images require
640x360 minimum and <=30 million pixels. MP4/WebM are decoded locally, source
audio removed and at most the first 20 seconds normalized to silent MP4. No
external network protocols are allowed in FFmpeg; conversion timeout is 45s.
Collection failure falls back to graphics without rerunning script generation.

Artifacts: `media-collection.json` (candidates, holds, reasons, network usage),
`auto-media-registry.json` (approved metadata, file hashes, exact narration
anchors) and `collected-media/`. Same-input attempts in the same output folder
are cached, including negative results. Automatic records explicitly have
`reviewed: false` and `approval: automatic-metadata-v1`; the renderer rechecks
the policy and file hash instead of pretending that a human inspected the file.
The existing manual registry and visually reviewed bundled library are separate.

One live read-only smoke check of the saved September 23 episode made six HTTP
requests and selected zero files: per-asset reuse permission was unavailable.
Mock tests cover actual download, integrity, automatic insertion and holds.
This does not promise footage availability for every day's selected stories.

Only `longform_system/` and its dedicated workflows are owned by this pipeline.
Source feed configuration and blocked-topic policy are read-only shared data.

Collected evidence requires a known publication time within the last seven days
and an extracted article body. KST today and source dates are supplied to the
writer and reviewer. Related coverage is retrieved where matching headlines exist;
missing responses must be disclosed, never invented. This is not a guarantee of
exhaustive research. Saved source bodies permit editorial inspection.

Publication requires 460–560 spoken Korean eojeol, ordered sections and source labels,
three extraction hooks, no blocked topic or harmful wording, semantic reviews
at least 95/100 with every factual/date/balance/safety flag true, title review,
and a decoded 1920x1080 MP4 lasting 240–360 seconds with audible, synced audio.
Semantic grades are model judgments, not independent proof of truth.
The earlier 750–850 eojeol request conflicts with five-minute narration:
741 eojeol measured 452.88 seconds at the configured natural speaking rate.
Five-minute delivery takes priority; measured audio remains the final gate.
Review findings require literal quotes from both script and evidence before
they can trigger repairs. This checks traceability, not truth of the judgment.

The upload first reserves a date and source fingerprint through GitHub's atomic
file-create API. Any existing reservation blocks a new upload, including when
the previous upload outcome is unknown. Do not delete reservations to retry:
first reconcile the YouTube channel and run's upload receipt manually.

`daily-longform.yml` builds without posting unless explicitly enabled.
`publish-longform.yml` publishes the exact successful artifact after checking
its date, video digest and encoded media again. It does not regenerate text.
The upload-only YouTube OAuth scope does not permit channel-list reads.

Groq uses only LONGFORM_GROQ_API_KEY and a bounded per-run call budget.
Different keys do not imply different organizations/quotas. Same-organization
shorts and longform calls still share the provider limit; we cannot infer the
organization from secret names. No paid fallback is enabled.

기존 숏폼 패키지와 독립적으로 동작하는 롱폼 제작·승인·업로드 시스템의 작업 공간입니다.

`title_system.py`는 롱폼 계획 JSON을 받아 중립성, 주제 일치, 명확성, 제목 길이를 점수화하고 최종 제목·설명란·태그 패키지를 반환합니다. 출처 URL은 설명란에 자동으로 포함됩니다.

이 시스템은 영상 렌더링 품질 검사까지 통과한 결과만 업로드 대상으로 넘깁니다.

### 연속 설명 화면 (visual version 3)

`illustrations/manifest.json`에 built-in image_gen으로 만든 6개 원본(외교, 입법,
경제, 무역, 시민 생활, 자료 검토), 정확한 프롬프트, SHA-256과 시각 확인 기록을
보관합니다. 이는 실제 사건의 근거가 아닌 재사용 가능한 설명 일러스트입니다.
원본 파일은 Git에 함께 저장되어 PC/Codex가 꺼져도 GitHub 렌더가 사용할 수 있습니다.

검증된 실제 자료가 있으면 이를 우선하며, 나머지 핵심 장면과 일부 연결 장면에는
문장 주제에 맞는 그림을 배치합니다. 원문 출처 카드, 맥락을 포함한 수치 화면,
3개 이슈 요약 화면을 번갈아 사용합니다. 미지의 주제에는 자료 검토 그림을
사용하고, 모든 장면을 그림/자료/그래픽으로 채웁니다. 정지 화면은 약 6초마다
구도를 바꾸며 자막·출처는 고정합니다. 이는 생성 동영상이 아니라 정지 이미지의
모션 편집입니다. 같은 원본의 여러 컷을 여러 신규 생성 이미지라고 세지 않습니다.

일일 작업은 새 이미지 API를 호출하지 않습니다. 이 채팅의 이미지 생성 기능을
GitHub에 자동 연결한 것이 아니며, 매일 사건별 새 AI 그림을 대량 생성하지는 않습니다.
AI 그림에는 실제 현장 아님 표시를 화면/설명란에 넣고 실제 자료 권리 검사는 유지합니다.
이미지 편집 컷 추가로 LLM/TTS 호출을 늘리지 않습니다.

### 코드 기반 설명 동영상 (visual version 4)

`motion.py`는 화물선·물결·크레인·컨테이너가 개별적으로 움직이는 8초
1920x1080/30fps 무음 MP4를 만듭니다. Sora/Veo 영상 생성 API가 아니라
코드로 제작하는 모션그래픽이며 별도 서비스 크레딧을 사용하지 않습니다.
공급망·물류 관련 내레이션이 있는 설명 그림 장면만 대체하고, 실제 자료와
출처/수치 화면은 우선 유지합니다. 오디오 길이는 바꾸지 않고 필요 시 반복합니다.
완성된 모션은 실행 작업 폴더에 캐시하고, 실패한 partial 파일은 사용하지 않습니다.
화면과 설명란에 개념 설명이며 실제 사건 현장·운송 경로가 아님을 표시합니다.

### 상황별 연속 배경 (visual version 5)

일반 설명에는 외교/입법/경제/시민 생활/물류/자료 확인의 6종 배경 모션을
문장 주제에 맞춰 자동 배치합니다. 알 수 없는 주제는 자료 확인으로 대체합니다.
출처·수치·권리 확인된 실제 자료·요약 첫 화면은 읽기 쉬운 전경으로 유지합니다.
선택 이유는 storyboard 및 render manifest의 visual_priority/selection_reason에 남습니다.
같은 테마가 이어지면 누적 음성 시간으로 재생 위치를 이어 가며, 테마별 8초 MP4를
한 실행에서 한 번만 렌더합니다. GitHub의 기존 daily-longform 경로가 자동 사용하며
외부 영상 API나 새로운 예약 작업은 필요하지 않습니다.
