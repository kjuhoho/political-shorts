# 내 목소리 만들기 (Piper 파인튜닝)

GPU 없이, Python 3.14 그대로 두고 **내 목소리로 내레이션**을 뽑는 방법.
학습만 무료 Colab GPU에서 한 번 돌리고, 이후 추론은 이 PC에서 오프라인·무료.

```
[1] 낭독 녹음  ──▶  [2] 데이터셋 검증  ──▶  [3] Colab 파인튜닝  ──▶  [4] .env 연결
 enrollment_ko.txt   voice_build_dataset.py   finetune_piper.ipynb    TTS_PROVIDER=piper
```

---

## 1. 녹음

**세부 체크리스트는 [RECORDING_CHECKLIST.md](RECORDING_CHECKLIST.md)** 를 그대로 따라가세요.
요점만:

- `enrollment_ko.txt`의 문장을 **한 문장 = 한 파일**로: `01.wav`, `02.wav` … (두 자리)
- 조용한 방, 입과 마이크 15~20cm 고정, 하루에 몰아서.
- **평소 쇼츠 내레이션 톤**. 너무 또박또박 X, 말하듯이.
- 포맷 자유(wav/flac/m4a/mp3) — 다음 단계가 22kHz mono로 변환.
- 40문장 최소, 50문장(20~30분) 권장. 여유되면 내 실제 대본도 `51.wav`…로 추가.
- `01.wav`만 먼저 녹음 → 2번 돌려서 `<clip>`/`<low>` 경고 없는지 확인 후 나머지.

## 2. 데이터셋 검증 + 변환

```powershell
.\.venv\Scripts\python.exe -m political_shorts voice dataset --audio recordings\ --zip
```

- `recordings\NN.wav` → `dataset\wav\NN.wav` (22050Hz mono 16bit, 앞뒤 무음 정리)
- `dataset\metadata.csv` (`NN|문장`, Piper 전처리가 읽는 LJSpeech 레이아웃)
- `--zip` 이면 `dataset.zip` 까지 — Colab 에 그대로 업로드.
- 콘솔에 파일별 길이·피크, `<clip>`(너무 큼) / `<low>`(너무 작음) 경고, 총 분량.
- ffmpeg만 사용 — 추가 파이썬 패키지 불필요.
- (동일: `python scripts\voice_build_dataset.py --audio recordings\`)

## 3. Colab에서 파인튜닝

1. [Google Colab](https://colab.research.google.com) → `finetune_piper.ipynb` 업로드 → 런타임 유형을 **GPU(T4)** 로.
2. `dataset\` 폴더를 통째로 zip해서 노트북 첫 셀 안내대로 업로드(또는 Google Drive).
3. 셀을 위에서 아래로 실행. 한국어 사전학습 체크포인트에서 이어 학습하므로
   **1000~2000 epoch(무료 T4로 1~3시간)** 면 충분합니다. 중간 샘플을 들어보고
   만족스러우면 멈춰도 됩니다.
4. 마지막 셀이 `myvoice.onnx` + `myvoice.onnx.json` 을 내려줍니다.

> 파인튜닝 레시피가 Piper 버전마다 조금씩 바뀝니다. 노트북이 특정 커밋을
> 고정(pin)하고, 안 되면 공식 [Piper TRAINING.md](https://github.com/OHF-Voice/piper1-gpl/blob/main/TRAINING.md)
> 절차로 대체하도록 주석을 달아 두었습니다.

## 4. 이 PC에 연결

1. piper 실행파일 받기 —
   <https://github.com/OHF-Voice/piper1-gpl/releases> 의 `piper_windows_amd64.zip`
   압축 해제 (예: `D:\political-shorts\tools\voice\piper\piper.exe`).
2. `myvoice.onnx` + `myvoice.onnx.json` 을 `models\` 에 복사.
3. `.env`:
   ```ini
   TTS_PROVIDER=piper
   VOICE_PIPELINE=true
   PIPER_BIN=tools\voice\piper\piper.exe
   PIPER_MODEL=models\myvoice.onnx
   ```
4. 점검 + 들어보기:
   ```powershell
   .\.venv\Scripts\python.exe -m political_shorts voice doctor
   .\.venv\Scripts\python.exe -m political_shorts voice preview "오늘의 정치 뉴스를 정리해 드리겠습니다" --all-roles
   ```

---

## 학습 전에 미리 테스트하고 싶다면 (선택)

클라우드 zero-shot 클론 엔드포인트(RunPod/Replicate에 XTTS-v2·GPT-SoVITS 배포)를
띄우고 짧은 레퍼런스 클립만 주면, 학습 없이 목소리를 흉내낸 합성을 받을 수 있습니다.

```ini
TTS_PROVIDER=myvoice
VOICE_MYVOICE_BACKEND=xtts_api
VOICE_PIPELINE=true
XTTS_API_URL=https://<your-endpoint>/tts
XTTS_SPEAKER_WAV_URL=https://<host>/my_reference_30s.wav
```

엔드포인트는 `{ "text": "...", "language": "ko", "speaker_wav_url": "..." }` 를 받아
`audio/wav` 바이트나 `{ "audio_base64": "..." }` 를 돌려주면 됩니다.
품질·일관성은 파인튜닝한 Piper 쪽이 낫습니다 — 어디까지나 임시용.
