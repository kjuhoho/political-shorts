---
name: longform-explainer-video
description: 오늘의엔터 롱폼(5분 정치 브리핑)의 상황 설명 화면과 AI 일러스트 팩을 만들고 개선하는 절차. "롱폼 화면 개선", "설명 그림 추가", "일러스트 팩", "상황 설명 영상", "롱폼 미리보기" 같은 요청이나 longform_system의 visuals.py / illustrations.py / motion.py를 만질 때 사용한다. Use when working on the longform channel's explainer visuals, illustration pack or scene planning.
---

# 롱폼 상황 설명 영상 (방식 A)

**한 줄 원칙:** 대본에서 검증된 문장 + 주제에 맞는 AI 정지 그림 + 천천히 확대·이동 + 출처·수치 카드. 생성형 영상(Veo, Kling 등)은 쓰지 않는다. 5분에 약 2,400~3,400크레딧이 들고 현재 플랜으로 실행할 수 없으며, 정치 뉴스에 가짜 현장 영상이 들어갈 위험이 있다.

시작 전에 `longform_system/AGENTS.md`의 2026-11-24 항목과 `longform_system/README.md`를 읽는다. 이 스킬은 그 내용을 대체하지 않고 작업 순서만 잡아 준다.

## 지켜야 할 것

- 사람, 깃발, 로고, 글자, 숫자, 차트, 실제 장소·사건처럼 보이는 그림을 만들거나 등록하지 않는다. 실제 인물 얼굴 합성 금지.
- AI 그림에는 항상 "AI 생성 · 실제 현장 아님" 표시를 유지한다(화면 하단 + 설명란 `credits()`).
- 화면에 새 사실을 만들지 않는다. 글자는 검수를 통과한 대본 문장과 원문 인용에서만 가져온다. 설명용 구조화(타임라인·비교)는 실제 대본에서 해당 문장이 영상당 1건 안팎이라 만들지 않기로 했다.
- LLM·TTS 호출을 늘리지 않는다. Groq 하루 한도가 빠듯하다(정상 한 번이 약 13호출, 5만 토큰). 시각 작업은 전부 오프라인으로 검증한다.
- 사람이 눈으로 확인하지 않은 그림을 등록하지 않는다(`visually_checked`는 사람의 확인을 뜻한다).
- 사용자 PC에 파일을 쌓지 않는다. 임시 파일(내려받은 아티팩트, 미리보기, worktree)은 작업이 끝나면 직접 지우고, 사용자 소유의 오래된 산출물(`output/` 등)은 묻지 않고 지우지 않는다.
- 로컬 작업 트리에 Codex가 남긴 수정(`state/uploads` 삭제, `last_success_date.txt` 등)이 있다. 커밋에 섞지 않는다. 항상 `origin/main`에서 새 worktree를 만들어 거기서 작업·푸시한다.

## 코드 지도

| 파일 | 역할 |
|---|---|
| `visuals.py` `plan()` | 문장 단위 장면 종류 결정(source/number/explanation/media/overview/motion) → `attach_illustrations()`가 일반 장면을 그림 구간으로 바꿈 |
| `illustrations.py` | `pack_spec.json` 읽기, `library()`(해시·확인된 파일만), `pick()`(키워드 점수 + 반복 감점 + 날짜 시드) |
| `illustrations/pack_spec.json` | 그림 30종의 id·캡션·키워드·프롬프트 주제. 새 24종은 파일이 없으면 선택되지 않음 |
| `illustrations/manifest.json` | 등록된 그림의 SHA-256·프롬프트·확인 기록 |
| `register_illustrations.py` | 프롬프트 파일 생성, 파일 검사, 모음 이미지, 승인 등록 |
| `motion.py` | 코드 모션 배경. 지금은 공급망 문장에만 쓰임 |
| `renderer.py` | 장면별 ffmpeg 합성. 그림은 확대·이동, 글자 레이어는 고정 |

## 작업별 절차

### 1. 그림 추가 (사용자 PC에 용량이 쌓이지 않게)
사용자는 PC에 파일이 쌓이는 것을 원하지 않는다. 그림은 **저장소 밖 임시 받는 폴더**(기본 `D:\illustration-inbox`)에서만 받고, 저장소 반영이 끝나면 지운다. 로컬 작업 트리에는 그림을 두지 않는다(오래됐고 Codex 수정이 섞여 있어 나중에 pull이 충돌한다).
1. `python -m longform_system.register_illustrations --write-prompts D:\illustration-inbox\PACK_PROMPTS.md` (폴더가 없으면 만든다)
2. 이미지 도구(Codex built-in image_gen)가 `<id>.png`를 그 폴더에 저장한다. 이미지 도구는 사용자가 Codex에서 돌린다. 나는 이미지 생성 도구가 없으니 만들었다고 말하지 않는다.
3. `origin/main`에서 새 worktree를 만들고 `python -m longform_system.register_illustrations --check --sheet <모음.png> --inbox D:\illustration-inbox` 로 크기·비율·빈 이미지·3.5MB 초과를 거른다.
4. **모음 이미지를 직접 열어 한 장씩 본다.** 체크리스트: 사람/얼굴 없음, 글자·숫자·로고·깃발 없음, 실제 장소처럼 보이지 않음, 기존 6장과 색감·질감이 맞음, 주제가 캡션과 맞음, 가운데 피사체가 잘리지 않음. 하나라도 어긋나면 그 id는 다시 생성한다.
5. 통과한 것만 `--approve id id ... --inbox D:\illustration-inbox` (받는 폴더에서 worktree로 복사 후 등록). 키워드를 손볼 일이 있으면 `pack_spec.json`을 고친다(너무 흔한 단어 금지: 보도, 정부, 정책, 발표 등).
6. 롱폼 테스트 → 커밋 → 푸시 → `origin/main`에 파일이 올라갔는지 확인.
7. **정리:** `--clean-inbox --inbox D:\illustration-inbox` 는 저장소 복사본과 바이트가 같은 등록 그림만 지우고, 아무것도 안 남으면 폴더까지 지운다(미등록·수정된 그림은 남긴다). 이어서 worktree(`git worktree remove --force`)와 scratchpad의 내려받은 아티팩트·미리보기를 지운다. 사용자에게 정리한 것과 남긴 것을 알린다.

### 2. 장면 규칙 바꾸기
`visuals.py`를 고치고 `plan()` 결과의 `version`을 올린다. 테스트는 숫자를 모듈 상수에서 읽지 말고 문자 그대로 쓴다(모듈 값을 읽으면 상수를 바꿔도 통과한다). 변이 테스트로 새 테스트가 실제로 실패하는지 확인한다.

### 3. 미리보기(오프라인, 비용 0)
- 실제 대본과 `sources.json`이 있는 폴더(예: GitHub Actions 아티팩트 `art/`)에서 `clean_script()` → `visuals.plan()` → `draw_scene()`으로 장면을 그려 한 장에 모은다. 과거 비교 시트 예: 코드 배경과 그림 방식을 나란히.
- 전체 팩을 가정한 시뮬레이션: `illustrations.library`를 `unittest.mock.patch.object`로 30종 키워드 사전으로 바꿔 `plan()`을 돌린다. 영상당 서로 다른 그림 수와 구간별 선택 이유를 본다. 시뮬레이션은 키워드 결과일 뿐이며 실제 그림이 등록되기 전에는 영상에 반영되지 않는다고 보고한다.
- 과거 음성 재사용 렌더: `python -m longform_system.visual_preview --bundle <렌더 번들> --output <폴더> --ffmpeg <경로>`. 이 결과는 신규 뉴스로 게시하지 않는다.

### 4. 영상 한 편 실제 제작(수동)
GitHub Actions `daily longform`을 `workflow_dispatch`로 실행하면 된다. 입력은 `publish`(false면 아티팩트만 만들고 업로드하지 않음)와 `resume_run`(같은 날 초안을 고쳐 다시 검수). 한 번에 LLM 약 13호출을 쓰므로 하루 한도를 먼저 확인하고, 자동 실행이 이미 `generation-<오늘>.json`을 예약했으면 겹치지 않게 한다. 워크플로가 "success"여도 게시를 뜻하지 않으니 `longform-<날짜>.json` 영수증과 `published` 기록으로 확인한다. 예약 시각은 워크플로상 06:20 UTC(15:20 KST)이나 GitHub 지연으로 최근에는 23:30~04:00 KST쯤 시작했다.

## 보고할 때

- 실제로 확인한 것과 시뮬레이션을 구분한다. 키워드 시뮬레이션은 "그림이 들어오면 이렇게 된다"이지 "이미 이렇다"가 아니다.
- 같은 그림의 확대·이동을 새 원본 수로 세지 않는다.
- 게시 성공은 GitHub 실행 결과가 아니라 영수증으로 말한다.
