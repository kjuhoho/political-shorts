# 음성 파이프라인 (Voice)

숏폼에서 음성이 "로봇 같다 / 호흡이 없다 / 숫자·이름을 틀리게 읽는다"는 문제를
**엔진 교체가 아니라 엔진 앞뒤 처리**로 푼다. 어떤 TTS(무료 `edge` 포함)를 쓰든
같은 3단계를 통과한다.

```
script_gen 의 narration
      │
   ┌──▼───────────────────────────────────────────────┐
   │ L1  voice/normalize.py   (순수 파이썬, 3.14 OK)   │
   │   · 숫자/%/조억/날짜/시각 → 한글 읽기 (numbers.py) │
   │   · 정치 고유명사·영문약어 발음 사전 (lexicon.py)  │
   │   · …/— → 쉼표 호흡, 문장 종결부호 보정            │
   │   · 카드 role별 속도·쉼 힌트 (Prosody)             │
   └──┬───────────────────────────────────────────────┘
      │ speak text (+ context 로 Prosody 전달)
   ┌──▼───────────────────────────────────────────────┐
   │ L2  엔진  (tts_providers.REGISTRY)                │
   │   edge / azure / gcloud / elevenlabs / openai     │
   │   piper    ← 내 목소리, 오프라인, CPU 실시간       │
   │   myvoice  ← piper 또는 클라우드 zero-shot 클론    │
   └──┬───────────────────────────────────────────────┘
      │ seg_NN.(mp3|wav)
   ┌──▼───────────────────────────────────────────────┐
   │ L3  voice/post.py   (ffmpeg만)                    │
   │   앞뒤 무음 트림 → 컴프 → 디에싱 → loudnorm(-14)  │
   │   voice/qa.py (선택): Whisper 재전사 ↔ 대본 대조   │
   └──────────────────────────────────────────────────┘
```

`VOICE_PIPELINE=true` 한 줄로 L1+L3 가 켜진다. `tts.synthesize_segments` 가
엔진 호출 전에 `voice.prepare()`, 후에 `voice.post.process()` 를 부른다.
꺼져 있으면(`false`, 기본값) 기존 동작과 100% 동일.

---

## 지금 바로 (엔진은 그대로, 자연스러움만)

```powershell
# .env
VOICE_PIPELINE=true      # TTS_PROVIDER 는 edge 그대로 둬도 됨

.\.venv\Scripts\python.exe -m political_shorts voice preview "국민의힘 108석, 27.3%p 격차" --all-roles
.\.venv\Scripts\python.exe -m political_shorts voice doctor      # 체인 점검
```

`preview_edge_hook.wav` … 6개(역할별)가 `output\voice_preview\` 에 생긴다.
`--all-roles` 로 hook(빠르고 높게) vs factcheck(느리고 또박또박) 차이를 비교.
(`scripts\voice_preview.py` 도 같은 일을 하는 얇은 래퍼 — 둘 중 아무거나.)

## 내 목소리로 (piper)

녹음 방법은 **[tools/voice/RECORDING_CHECKLIST.md](tools/voice/RECORDING_CHECKLIST.md)**,
전체 절차는 **[tools/voice/README.md](tools/voice/README.md)**. 요약:

| 단계 | 명령 / 도구 | 결과 |
|---|---|---|
| 녹음 | `tools/voice/enrollment_ko.txt` 50문장 → `recordings\01.wav`… | 20~30분 음성 |
| 검증 | `political_shorts voice dataset --audio recordings\ --zip` | `dataset\` + `dataset.zip` |
| 학습 | `tools/voice/finetune_piper.ipynb` (Colab T4, 1회; `MODE=finetune`\|`scratch`) | `myvoice.onnx (+ .json)` |
| 연결 | `.env`: `TTS_PROVIDER=piper` `PIPER_BIN=…` `PIPER_MODEL=models\myvoice.onnx` | 오프라인·무료 합성 |
| 점검 | `political_shorts voice doctor` | 빠진 것 표시 |

학습 전 임시 테스트는 클라우드 zero-shot(`VOICE_MYVOICE_BACKEND=xtts_api`) — README 참고.

---

## 설정 (`.env`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `VOICE_PIPELINE` | `false` | L1+L3 마스터 스위치 |
| `VOICE_LUFS` | `-14` | 내레이션 트랙 최종 라우드니스 |
| `VOICE_QA` | `false` | Whisper 재전사 대조(로그만). `faster-whisper` 필요 → 별도 3.11 venv |
| `VOICE_QA_MODEL` / `VOICE_QA_THRESHOLD` | `small` / `0.72` | QA 모델 크기 / 일치율 경고선 |
| `PIPER_BIN` | `piper` | piper 실행파일 경로 |
| `PIPER_MODEL` | `models\myvoice.onnx` | 학습된 음성 모델 |
| `PIPER_LENGTH_SCALE` | `1.0` | 전체 템포(>1 느리게). role 힌트가 곱해짐 |
| `PIPER_NOISE_SCALE` / `PIPER_NOISE_W` | `0.667` / `0.8` | 표현 다양성 / 발음 흔들림 |
| `VOICE_MYVOICE_BACKEND` | `piper` | `piper` \| `xtts_api` |
| `XTTS_API_URL` / `_TOKEN` / `XTTS_SPEAKER_WAV_URL` / `_ID` | — | 클라우드 zero-shot 엔드포인트 |

## 역할별 딜리버리 (`voice/normalize.py` `_PROSODY`)

| role | length_scale | pitch | sentence_silence |
|---|---|---|---|
| hook | 0.96 (빠름) | +1 | 0.28 |
| summary | 0.99 | 0 | 0.34 |
| what | 1.00 | 0 | 0.40 |
| reaction | 1.00 | −1 | 0.38 |
| factcheck | 1.05 (느림) | 0 | 0.46 |
| outro | 1.02 | 0 | 0.40 |

- **piper**: `length_scale` × 역할배수 + `sentence_silence` 직접 반영.
- **edge**: 역할별 `rate`(base rate에 곱연산) + `pitch`(Hz 근사) 적용.
- **azure / gcloud**: `voice/ssml.py` 가 `<prosody rate/pitch>` + 문장 사이
  `<break>` + 대비어(하지만/그런데…) 뒤 짧은 `<break>` + 따옴표 구간
  `<emphasis>` 로 SSML 생성 (숫자는 L1이 이미 풀어써서 `<say-as>` 불필요).

## 발음 사전

- 표준: `config/voice_lexicon.yaml` (git 추적). 정당·기관 정식명칭의 '소리',
  영문약어 자모 읽기, 역사적 날짜(5·18→오일팔), 행정용어.
- 내 채널 전용: `config/voice_lexicon.local.yaml` (git 무시). 같은 키는 local 우선.
- 긴 표기부터 매칭. `NEUTRALITY.md` 원칙상 진영 별칭·비하 표현은 넣지 않는다.
- 사전에 없는 대문자 2~6자 약어는 자동으로 자모 낭독(`GTX`→`지티엑스`).

## 코드 지도

| 파일 | 역할 |
|---|---|
| `voice/numbers.py` | 숫자 → 낭독체 (%, 조/억, 날짜, 시각=native, 비율, 범위) |
| `voice/lexicon.py` | YAML 사전 로드·치환 + 약어 자모 낭독 |
| `voice/normalize.py` | L1 오케스트레이션 + `Prosody`/`Prepared` |
| `voice/ssml.py` | azure/gcloud 용 SSML (`<prosody>`/`<break>`/`<emphasis>`) |
| `voice/providers_myvoice.py` | `piper` / `xtts_api` / `myvoice` 백엔드 |
| `voice/post.py` | L3 ffmpeg 후처리 |
| `voice/qa.py` | 선택 Whisper 재전사 대조 |
| `voice/context.py` | 오케스트레이터→엔진 Prepared/Prosody 전달 (스레드 로컬) |
| `voice/tools.py` | `preview()` / `doctor()` — 스크립트와 CLI 공용 |
| `tts.py` | `synthesize_segments(..., roles=)` 에서 L1/L3 훅 |
| `cli.py` | `political_shorts voice doctor\|preview\|dataset` |
| `scripts/voice_preview.py` · `voice_build_dataset.py` | 위 CLI의 얇은 래퍼 / 데이터셋 빌더 |
| `tools/voice/` | 낭독 대본 · RECORDING_CHECKLIST.md · Colab 노트북 |

## 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_voice.py tests\test_tts.py
```
