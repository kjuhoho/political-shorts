# 오늘의엔터 롱폼 시스템

## Publication contract

Only `longform_system/` and its dedicated workflows are owned by this pipeline.
Source feed configuration and blocked-topic policy are read-only shared data.

Collected evidence requires a known publication time within the last seven days
and an extracted article body. KST today and source dates are supplied to the
writer and reviewer. Related coverage is retrieved where matching headlines exist;
missing responses must be disclosed, never invented. This is not a guarantee of
exhaustive research. Saved source bodies permit editorial inspection.

Publication requires 750–850 spoken words, ordered sections and source labels,
three extraction hooks, no blocked topic or harmful wording, semantic reviews
at least 95/100 with every factual/date/balance/safety flag true, title review,
and a decoded 1920x1080 MP4 lasting 240–360 seconds with audible, synced audio.
Semantic grades are model judgments, not independent proof of truth.

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
