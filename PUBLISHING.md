# 게시(업로드) 연동 가이드

기본값은 **꺼짐**입니다. `.env`에서 `ENABLE_PUBLISH=true`로 바꾸기 전까지 모든
게시 어댑터는 dry-run 로그만 남깁니다.

```powershell
pip install -r requirements-publish.txt   # 또는 install.ps1 -WithPublish
```

세 플랫폼을 한 번에 다 켤 필요 없습니다. **YouTube부터** 하나씩 붙이세요.
`ENABLE_PUBLISH=true`인 상태에서 켜지지 않은(미설정) 어댑터는 자동으로 건너뜁니다.

> 정치 콘텐츠 주의: 세 플랫폼 모두 선거·정치광고·합성미디어 정책이 있고, 국내
> 선거기간에는 공직선거법(딥페이크 표시의무, 허위사실공표)이 적용됩니다.
> 완전 무인보다 **게시 직전 사람 검토 1단계**를 두는 것을 강력히 권합니다.

> PowerShell 기준입니다. `cd D:\political-shorts` (cmd의 `cd /d`는 PowerShell에서 안 됨).

---

## 1. YouTube (무인 자동화 가능) — ✅ 이 프로젝트에서 실제 업로드 검증됨

> 준비물: 업로드할 **YouTube 채널이 있는 구글 계정**. 채널이 없으면 youtube.com에서
> 먼저 만드세요. 브랜드 채널이면 1-7 참고.

### 1-0. 게시 라이브러리 설치 (한 번만)

```powershell
cd D:\political-shorts
.\.venv\Scripts\python.exe -m pip install -r requirements-publish.txt
```

### 1-1. Google Cloud 프로젝트

1. https://console.cloud.google.com → 채널 관리 구글 계정으로 로그인
2. 상단 파란 바 **프로젝트 선택 ▾ → 새 프로젝트** → 이름 `political-shorts` → 만들기
3. 만들어지면 **다시 상단 ▾ 에서 political-shorts 프로젝트 선택**
   (이후 모든 작업이 이 프로젝트 안에서 진행돼야 함)

### 1-2. YouTube Data API v3 켜기

☰ → **API 및 서비스 → 라이브러리** → `YouTube Data API v3` 검색 → **사용 설정(Enable)**

### 1-3. OAuth 동의 화면

☰ → **API 및 서비스 → OAuth 동의 화면**
(최신 UI는 "Google 인증 플랫폼 → 시작하기"로 안내)

1. 앱 이름 `political-shorts`, 사용자 지원 이메일 = 본인
2. **대상(Audience): 외부(External)**
3. **개발자 연락처 정보**에 본인 이메일 입력 → 저장

만든 뒤 **대상(Audience)** 탭에서 두 갈래:

| | **A) 프로덕션 게시 (권장)** | B) 테스트 상태 유지 |
|---|---|---|
| 방법 | 브랜딩 페이지 필수값 채우고 **앱 게시** 클릭 | **테스트 사용자 → 사용자 추가**에 내 이메일 |
| 리프레시 토큰 | **만료 안 됨** ✅ | **7일 후 만료** → 7일마다 재인증 |
| 심사 | 불필요(미확인·본인 100명 이내) | 불필요 |

> **앱 게시** 버튼이 회색이면 브랜딩 페이지 필수값이 빔. **브랜딩으로 이동** →
> 맨 아래 **개발자 연락처 정보**에 이메일 입력, 로고·앱 도메인은 비워두고 저장 →
> 다시 대상 탭에서 앱 게시.

매일 자동 업로드가 목표면 **A**. B면 스케줄러가 일주일 뒤 `invalid_grant`로 멈춤.

### 1-4. OAuth 클라이언트 ID (데스크톱 앱)

☰ → **API 및 서비스 → 사용자 인증 정보** → **+ 사용자 인증 정보 만들기 → OAuth 클라이언트 ID**

- **애플리케이션 유형: 데스크톱 앱** ← 반드시. (웹으로 만들면 `redirect_uri_mismatch`)
- 이름 아무거나 → 만들기 → 팝업/목록에서 **JSON 다운로드**

### 1-5. 파일 배치 & .env

1. 받은 JSON을 이 경로/이름으로 저장:
   ```
   D:\political-shorts\secrets\client_secret_youtube.json
   ```
   PowerShell로 다운로드 폴더에서 자동 이동:
   ```powershell
   $src = Get-ChildItem "$env:USERPROFILE\Downloads\client_secret_*.json" |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
   Copy-Item $src.FullName "D:\political-shorts\secrets\client_secret_youtube.json" -Force
   ```
2. `D:\political-shorts\.env`:
   ```
   ENABLE_PUBLISH=true
   YOUTUBE_CLIENT_SECRET_FILE=secrets\client_secret_youtube.json
   YOUTUBE_TOKEN_FILE=secrets\token_youtube.json
   YOUTUBE_PRIVACY_STATUS=private      # 며칠 확인 후 public 또는 unlisted
   YOUTUBE_CATEGORY_ID=25              # 25 = News & Politics
   ```
3. 확인:
   ```powershell
   .\.venv\Scripts\python.exe -m political_shorts doctor
   # publish enabled : True  이면 OK
   ```

### 1-6. 최초 브라우저 인증 (한 번만)

**먼저**: `doctor`에 `publish enabled : True`, 그리고 1-3에서 "앱 게시" 또는
"테스트 사용자에 내 계정 추가" 중 하나를 끝냈어야 함.

```powershell
cd D:\political-shorts
.\.venv\Scripts\python.exe -m political_shorts auth youtube
```

터미널에 먼저:
```
Opening a browser for Google sign-in / consent ...
브라우저에서 Google 로그인/동의를 진행하세요. 자동으로 안 열리면 이 URL을 여세요:
https://accounts.google.com/o/oauth2/auth?...&access_type=offline&prompt=consent
```
- **Windows 방화벽 팝업** 뜨면 "액세스 허용"(개인 네트워크). localhost 콜백용.
- 브라우저 자동 실행 안 되면 위 URL 직접 붙여넣기.

**브라우저 화면 순서:**

1. **계정 선택** — 업로드할 채널의 구글 계정 (테스트 사용자로 등록한 그 계정).
   브랜드 채널이면 → 1-7
2. **"Google에서 아직 이 앱을 확인하지 않았습니다"** 회색 화면
   → 좌하단 **고급(Advanced)** → **"political-shorts(으)로 이동(안전하지 않음)"**
   - `고급`이 없으면 앱이 "내부"거나 내 계정이 테스트 사용자가 아님 → 1-3 확인
3. **"YouTube 동영상 업로드 및 관리"** 체크 → **계속(Continue)**
4. 브라우저: **"인증 완료. 이 창을 닫고 터미널로 돌아가세요."** → 탭 닫기
5. 터미널:
   ```
   OK. Token cached at secrets\token_youtube.json
      valid=True scopes=['https://www.googleapis.com/auth/youtube.upload']
   ```

**확인:**
```powershell
dir secrets   # client_secret_youtube.json + token_youtube.json 둘 다
# token_youtube.json 안에 "refresh_token" 항목이 있어야 무인 운영 가능
```
없으면 `del secrets\token_youtube.json` 후 `auth youtube` 재실행.

### 1-7. 브랜드 채널에 올리려면

- **계정 선택** 화면에 브랜드 채널이 **별도 항목**으로 뜸 → 그걸 선택 (토큰이 그 채널에 묶임)
- 안 뜨면: 그 브랜드 채널에 내 계정이 소유자/관리자로 없어서.
  https://studio.youtube.com → (브랜드 채널로) **설정 → 권한**에서 내 계정 관리자 추가 →
  `del secrets\token_youtube.json` → `auth youtube` 재실행
- 개인 채널 하나면 이 단계 무시

### 1-8. 첫 업로드 테스트

```powershell
cd D:\political-shorts
.\.venv\Scripts\python.exe -m political_shorts run --publish --max 1
# 오늘 수집분이 이미 있으면:
.\.venv\Scripts\python.exe -m political_shorts run --no-collect --publish --max 1
```

`--publish`를 줘도 `.env`의 `ENABLE_PUBLISH=true`가 아니면 dry-run.

출력 JSON의 `"publishes"`:
```jsonc
"publishes": [{
  "platform": "youtube",
  "status": "ok",                       // ok = 업로드 성공
  "dry_run": false,
  "remote_id": "EYHF5uY44LQ",
  "url": "https://youtube.com/shorts/EYHF5uY44LQ",
  "detail": "privacy=private"
}]
```
| `status` | 다음 행동 |
|---|---|
| `ok` | 성공. 아래 확인 |
| `dry-run` | `ENABLE_PUBLISH`가 아직 false → `.env` 고치기 |
| `disabled` | 설정 누락 → `detail` 확인 |
| `error` | `detail`을 1-9 표와 대조 |

**추가 확인:**
- https://studio.youtube.com → **콘텐츠** → 방금 영상이 **비공개**로 등장
  (몇 분 "처리 중" 정상)
- 세로+3분↓ 라 YouTube가 자동 Shorts 분류. 분류 전이면 `/shorts/` URL이 일반
  시청 페이지로 리다이렉트 — 정상.
- 로컬 기록:
  ```powershell
  .\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/political_shorts.sqlite3'); [print(r) for r in c.execute('select platform,status,remote_id,detail from publish_log order by id desc limit 5')]"
  ```
  또는 대시보드 "제작된 영상" 행.

**공개 전환**: 며칠 비공개로 확인 후 `.env` `YOUTUBE_PRIVACY_STATUS=public`(또는
`unlisted`) → **다음** 업로드부터 공개. 이미 올라간 영상은 YouTube Studio에서 직접.

**동작**: 로컬 mp4를 resumable upload로 채널에 업로드.
**쿼터**: 10,000 units/일, 업로드 1건 ≈ 1,600 → 하루 약 6편.
초과 시 API 및 서비스 → YouTube Data API → 할당량에서 증설 요청.

### 1-9. 에러별 상세 대응

**`redirect_uri_mismatch` / `Error 400`** — 클라이언트를 "웹 애플리케이션"으로
만듦. 삭제 → **데스크톱 앱**으로 새로 생성 → JSON 교체 → `auth youtube` 재실행.

**`Error 403: access_denied` / "이 앱은 차단되었습니다"** — 앱이 테스트 상태인데
내 계정이 테스트 사용자에 없음. **대상 → 테스트 사용자 → 사용자 추가**, 또는
**앱 게시**로 프로덕션 전환. 그 후 재실행.

**`invalid_grant: Token has been expired or revoked`** — 테스트 상태 7일 경과로
리프레시 토큰 만료. **프로덕션 전환** → `del secrets\token_youtube.json` → 재인증.
프로덕션이면 안 남.

**`403 ... "youtubeSignupRequired"`** — 그 계정에 YouTube 채널 없음.
youtube.com에서 채널 생성 후 재실행. (브랜드 채널 목표면 1-7)

**`quotaExceeded`** — 하루 업로드 한도(≈6편) 초과. 다음날(PT 자정) 리셋 대기
또는 할당량 증설 신청.

**`FileNotFoundError ... client_secret_youtube.json`** — 경로/이름 확인
(`D:\political-shorts\secrets\client_secret_youtube.json`). `auth youtube`는
반드시 `cd D:\political-shorts` 후 실행.

**`ModuleNotFoundError: googleapiclient` / `google_auth_oauthlib`** — 게시
라이브러리 미설치 → `.\.venv\Scripts\python.exe -m pip install -r requirements-publish.txt`

**브라우저가 안 열림(원격/서버)** — GUI 있는 PC에서 `auth youtube` 1회 →
`secrets\token_youtube.json`(+ client secret)을 서버 같은 경로로 복사.

**`insufficientPermissions` / insufficient authentication scopes** —
`del secrets\token_youtube.json` → 재인증.

**엉뚱한 채널에 올라감** — 계정에 채널 여러 개. `del secrets\token_youtube.json`
→ 재인증 → **계정 선택에서 원하는 채널 정확히 선택**.

---

## 2. Instagram Reels (공개 URL 호스팅 필요)

Graph API는 **파일 업로드를 받지 않고** 공개 https URL에서 영상을 당겨갑니다.

**1회 설정**
1. 인스타 계정을 **비즈니스/크리에이터**로 전환하고 **페이스북 페이지에 연결**
2. https://developers.facebook.com → 앱 생성(유형: 비즈니스)
3. 제품 추가: **Instagram Graph API** (+ Facebook Login)
4. 권한: `instagram_basic`, `instagram_content_publish`, `pages_show_list`,
   `business_management`
5. 그래프 API 탐색기 또는 앱 검수로 **장기(60일) 액세스 토큰** 발급
6. IG 비즈니스 계정의 **IG User ID** 확인
   (`GET /me/accounts` → 페이지 → `instagram_business_account`)
7. `output\`를 공개 https로 노출:
   ```powershell
   .\.venv\Scripts\python.exe -m political_shorts serve-media --port 8770
   # 다른 터미널에서
   cloudflared tunnel --url http://127.0.0.1:8770      # 또는  ngrok http 8770
   ```
8. `.env`:
   ```
   ENABLE_PUBLISH=true
   INSTAGRAM_ENABLED=true
   INSTAGRAM_USER_ID=<IG User ID>
   INSTAGRAM_ACCESS_TOKEN=<장기 토큰>
   PUBLIC_MEDIA_BASE_URL=https://<터널주소>      # 끝에 / 없이
   ```

**동작**: `POST /{ig-user}/media` (REELS, video_url) → 처리 완료 폴링 →
`POST /{ig-user}/media_publish`.
**한도**: 24시간 25건. 토큰 ~60일마다 갱신 필요.
**주의**: 무료 tier 터널 주소는 재시작 시 바뀝니다. 고정하려면 도메인+정적 호스팅 권장.

---

## 3. TikTok (앱 심사 전에는 비공개만)

**1회 설정**
1. https://developers.tiktok.com → 앱 생성
2. **Content Posting API** 추가, 스코프 `video.publish` (+ `video.upload`)
3. **도메인 검증**: `PUBLIC_MEDIA_BASE_URL`의 도메인을 개발자 포털에서 URL
   접두사 소유권 확인(메타태그/파일 업로드)
4. 사용자 액세스 토큰 발급(OAuth)
5. `.env`:
   ```
   ENABLE_PUBLISH=true
   TIKTOK_ENABLED=true
   TIKTOK_ACCESS_TOKEN=<user access token>
   PUBLIC_MEDIA_BASE_URL=https://<검증된 도메인>
   ```

**동작**: `POST /v2/post/publish/video/init/` (PULL_FROM_URL) → 상태 폴링.
**제약**: **미심사(unaudited) 앱은 `SELF_ONLY`(비공개)로만 게시됩니다.** 공개
게시하려면 앱을 제출해 심사를 통과해야 합니다. 어댑터는 안전하게
`privacy_level=SELF_ONLY`로 고정돼 있으니, 심사 통과 후
`src/political_shorts/publishers/tiktok.py`에서 값을 바꾸세요.

---

## 게시 결과 확인

- DB `publish_log` 테이블 / 대시보드 "제작된 영상" 행
- `run` 리포트 JSON의 `stories[].publishes[]`
- 각 어댑터는 실패해도 파이프라인을 멈추지 않고 로그만 남깁니다
  (`status`: `ok` | `error` | `dry-run` | `disabled`).

## 자동 게시까지 켠 일일 운영 예시

```powershell
# 수집·생성만 매일 (게시 X)
.\.venv\Scripts\python.exe -m political_shorts schedule add --at 07:30

# 아침에 대시보드에서 대본/영상 검토 후, 괜찮으면:
.\.venv\Scripts\python.exe -m political_shorts run --no-collect --publish
```

완전 무인을 원하면 `schedule add` 대상 스크립트를 `run --publish`로 바꾸면
되지만, 위 정치 콘텐츠 주의사항을 감안해 결정하세요.
