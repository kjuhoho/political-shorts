# political-shorts

도메인: **대한민국 국내 정치 뉴스 → 중립적 세로형 쇼츠 자동화** (개인용, 오프라인 우선)

공개된 언론 RSS를 수집해 같은 사안을 묶고, **사실 / 인용 / 해석**을 분리한
짧은 대본을 만든 뒤, 공정성·오보·유해표현 검사를 통과한 것만 1080×1920
MP4로 렌더링합니다. 게시(YouTube·Instagram·TikTok)는 **기본적으로 꺼져
있으며**(`ENABLE_PUBLISH=false`), 켜기 전까지 모든 게시 어댑터는 dry-run 로그만
남깁니다.

> ⚠️ 이 도구는 보도 요약 보조물입니다. 수치·인용은 반드시 원문으로 확인하세요.
> 자동 게시는 각 플랫폼 약관·저작권·선거법(예: 공직선거법상 딥페이크/허위사실)
> 준수 책임이 사용자에게 있습니다.

---

## 빠른 시작 (Windows 11)

```powershell
# 1) 압축을 C:\political-shorts 에 풀고 그 폴더에서:
powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallFfmpeg

# 2) .env 편집 (최소한 FONT_PATH, ENABLE_TTS 확인)
notepad .env

# 3) 환경 점검
.\.venv\Scripts\python.exe -m political_shorts doctor

# 4) 뉴스 수집 + 최대 MAX_ITEMS_PER_RUN개 쇼츠 생성 (게시 안 함)
.\.venv\Scripts\python.exe -m political_shorts run

# 5) 결과 확인용 대시보드
.\.venv\Scripts\python.exe -m political_shorts dashboard
#   -> http://127.0.0.1:8765
```

`install.ps1` 없이 수동 설치:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
copy .env.example .env
```

ffmpeg 설치:

```powershell
winget install --id Gyan.FFmpeg -e
```

---

## 파이프라인 단계

| # | 모듈 | 하는 일 |
|---|------|---------|
| 1 | `collect.py`    | `config/sources.yaml`의 RSS를 `requests`+`feedparser`로 수집, URL 정규화 후 SQLite에 중복 없이 저장 |
| 2 | `classify.py`   | 가중 키워드 사전으로 "국내 정치" 여부 판정 (해외정치·스포츠·증시 감점). LLM 키가 있으면 경계값만 보정 |
| 3 | `dedupe.py`     | 제목의 문자 3-shingle + 토큰 Jaccard + rapidfuzz 혼합 유사도로 같은 사안 묶기 (단일 기사도 크기 1 클러스터) |
| 4 | `analyze.py`    | 문장 단위로 **FACT / CLAIM / INTERPRETATION** 태깅 (인용동사·수치·날짜·전망표현 단서) |
| 5 | `script_gen.py` | 훅 → 사실 최대 3 → 인용 최대 2 → 해석 1(라벨 표시) → 출처 안내. LLM은 훅/아웃트로 문장 다듬기만 |
| 6 | `safety.py`     | **BLOCK**: 유해·비하 표현, 출처 없는 단정, 단일 출처 사실, 무출처 절대표현 / **WARN**: 한쪽 정당만 언급, 편향된 매체 구성, 자극적 표현 |
| 7 | `tts.py` + `tts_providers.py` | `TTS_PROVIDER`로 음성 엔진 선택: **edge**(무료·무키·MS 신경망), elevenlabs, azure, gcloud, openai, sapi. 실패 시 edge→SAPI→무음 자동 폴백 |
| 8 | `video.py`      | Pillow 카드 PNG → ffmpeg 클립화 → concat → (선택) BGM 믹싱. moviepy 불필요 |
| 9 | `metadata.py`   | 중립 제목/설명(전체 출처 목록·고지문)/태그/고정댓글 → `<video>.meta.json` |
| 10| `publishers/`   | YouTube(OAuth 재개형 업로드) · Instagram(Graph API, 공개 URL 필요) · TikTok(Content Posting API, 도메인 검증 필요). 전부 `ENABLE_PUBLISH` 게이트 |

`pipeline.py`가 위를 순서대로 실행하고 `jobs` 테이블에 실행 기록을 남깁니다.

---

## 음성(TTS) 바꾸기

`.env`의 `TTS_PROVIDER` 한 줄로 전환합니다. 기본값 `edge`는 **무료·API키 불필요**
(Microsoft 신경망 음성, `pip install edge-tts`만 있으면 됨).

| provider | 품질 | 비용 | 필요한 키 |
|---|---|---|---|
| `edge` (기본) | 매우 좋음 | 무료 | 없음 |
| `elevenlabs` | 최상 | 유료 | `ELEVENLABS_API_KEY` (+ `ELEVENLABS_VOICE_ID`) |
| `azure` | 최상급 | 무료티어→유료 | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` |
| `gcloud` | 좋음 | 무료티어 | `GOOGLE_TTS_API_KEY` |
| `openai` | 무난 | 유료 | `OPENAI_API_KEY` |
| `sapi` | 낮음(로봇) | 무료 | 없음 (오프라인 폴백) |

한국어 음성 예: edge/azure `ko-KR-SunHiNeural`·`ko-KR-InJoonNeural`,
gcloud `ko-KR-Neural2-A`. `TTS_VOICE`에 지정, 비우면 provider 기본값.
설정한 provider가 실패하면 자동으로 `edge` → `sapi` → 무음 순으로 폴백합니다.

## 배경음악(BGM)

1. 저작권 프리 트랙(mp3/m4a/wav)을 `assets\bgm\`에 넣습니다.
   (무료: YouTube 오디오 보관함, Pixabay Music, Uppbeat)
2. `.env`에서 `BGM_ENABLED=true`, `BGM_PATH=assets\bgm\내트랙.mp3`.
3. `BGM_VOLUME_DB`(-26=은은, -18=뚜렷), `BGM_DUCK=true`(나레이션 있을 때 자동 감쇠),
   `BGM_FADE_SECONDS`로 조정.

`assets\bgm\placeholder_bed.mp3`는 파이프라인 확인용 저음 드론입니다 — 실제
영상에는 반드시 교체하세요. 영상보다 짧은 트랙은 자동 루프됩니다.

---

## CLI

```text
python -m political_shorts collect                RSS 수집만
python -m political_shorts classify               미분류 기사 분류
python -m political_shorts cluster                 클러스터링만
python -m political_shorts run [--no-collect] [--publish] [--max N]
python -m political_shorts build <cluster_id>      특정 클러스터만 렌더
python -m political_shorts dashboard               로컬 대시보드
python -m political_shorts schedule add --at 07:30 [--no-collect]
python -m political_shorts schedule remove
python -m political_shorts schedule status
python -m political_shorts doctor                  환경 자체 점검
```

`--publish`를 줘도 `.env`의 `ENABLE_PUBLISH=true`가 아니면 dry-run입니다.

---

## 예약 실행 (Windows 작업 스케줄러)

```powershell
.\.venv\Scripts\python.exe -m political_shorts schedule add --at 07:30
# 또는
powershell -ExecutionPolicy Bypass -File .\scripts\register_task.ps1 -At 07:30
```

`schtasks.exe`로 현재 사용자 권한(`/RL LIMITED`)의 `PoliticalShortsDaily`
작업을 만들고, 매일 `scripts\run_pipeline.py`를 `pythonw.exe`로 실행합니다.
해제는 `schedule remove`.

---

## 게시 활성화 (선택, 각 플랫폼별로 하나씩)

1. `.env`에서 `ENABLE_PUBLISH=true`.
2. **YouTube**: Google Cloud에서 *YouTube Data API v3* 사용 설정 → "데스크톱 앱"
   OAuth 클라이언트 JSON 다운로드 → 경로를 `YOUTUBE_CLIENT_SECRET_FILE`에.
   첫 실행 시 브라우저 인증, 토큰은 `secrets\token_youtube.json`에 캐시.
   기본 공개범위는 `private`.
3. **Instagram**: 비즈니스/크리에이터 계정 + 장기 액세스 토큰
   (`instagram_content_publish`). Graph API는 파일 업로드를 받지 않고 **공개
   https URL**에서 영상을 당겨갑니다 → `./output`을 서빙하는 호스트를
   `PUBLIC_MEDIA_BASE_URL`에 설정.
4. **TikTok**: 개발자 앱 + `video.publish` 스코프. 미심사 앱은 `SELF_ONLY`
   (비공개)로만 게시됩니다. 도메인(URL 접두사) 소유권 검증 필요.

`requirements-publish.txt`도 설치: `pip install -r requirements-publish.txt`
(또는 `install.ps1 -WithPublish`).

---

## 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

- `test_classify.py` — 국내정치/스포츠/해외정치 분류
- `test_dedupe.py` — 유사 헤드라인 병합 / 상이 사안 분리
- `test_analyze.py` — 사실·인용·해석 태깅
- `test_safety.py` — 유해표현·단정·단일출처 차단, 편향 경고
- `test_pipeline_offline.py` — 가짜 기사 시딩 후 수집·렌더 제외 전 구간

`scripts\smoke_test.py`는 실제 RSS 수집 + (ffmpeg 있으면) MP4 생성 +
대시보드 `/health`까지 확인합니다.

---

## 폴더 구조

```
political-shorts/
├─ install.ps1              설치/부트스트랩
├─ pyproject.toml           패키지 정의 (src 레이아웃)
├─ requirements*.txt
├─ .env.example             설정 템플릿 (복사해서 .env)
├─ config/sources.yaml      RSS 소스 + 성향 라벨 + 키워드 힌트
├─ src/political_shorts/     본체 (위 표의 모듈들)
│  └─ publishers/            youtube / instagram / tiktok 어댑터
├─ scripts/
│  ├─ run_pipeline.py        예약 작업이 부르는 러너
│  ├─ run_dashboard.py
│  ├─ register_task.ps1
│  └─ smoke_test.py
├─ tests/
├─ data/                    SQLite DB (gitignore)
└─ output/                  생성된 mp4 + meta.json (gitignore)
```

---

## 알려진 한계 / 주의

- 분류·태깅은 **휴리스틱**입니다. 경계 사안은 대시보드에서 대본을 직접 확인하세요.
- `safety.py`는 안전망이지 편집자가 아닙니다. 게시 전 사람이 최종 검토하는 것을 전제로 합니다.
- 일부 언론사 RSS는 수시로 URL이 바뀝니다. `doctor`/`collect` 로그에서 실패 피드를 확인하고 `config/sources.yaml`을 갱신하세요.
- Instagram/TikTok은 공개 URL 호스팅이 없으면 dry-run 이상으로 진행되지 않습니다.
- 한국어 폰트가 없으면 자막이 깨집니다. Win11 기본 `malgun.ttf` 경로를 `.env`에서 확인하세요.
