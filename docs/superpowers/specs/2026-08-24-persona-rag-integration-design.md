# 페르소나 + RAG 통합 — 답글 톤 레이어 + 세일즈랩 색채 제거 + 직접 쓰기 초안 유실 수정

## 배경

`2026-08-21-llm-rag-reply-design.md`(코어 설계)로 불만 카테고리 리뷰는 실제
Claude API 기반 RAG 답글 생성이 이미 배포됐다. 그런데 답글 스타일(발랄
이모지 파티/진중맨/무난 요정/진지한 하이개그) 드롭다운은 `category ==
"no_issue"`(칭찬/무난 리뷰)일 때만 실제로 적용되고, 불만 카테고리 리뷰는
`generate_ai_reply`(RAG)가 `style_id`를 완전히 무시한 채 항상 사장님의 학습된
말투로만 생성한다(`backend/app/routers/reviews.py`의 `generate_reply` 분기,
`backend/app/llm/generate.py`) — 그래서 불만 리뷰에서 어떤 페르소나를 골라도
결과가 똑같은 기존 버그가 있다.

브레인스토밍 중 사용자가 4개 페르소나 이름/설명이 세일즈랩 화면에서 그대로
가져온 것임을 직접 확인해줬다 — 이 프로젝트는 "세일즈랩 화면 복제 금지,
분석 후 재설계" 원칙(CLAUDE.md)을 이미 UI 스크린 단위로 지켜왔는데, 페르소나
캐릭터 설정 자체도 같은 원칙을 적용하기로 했다. 여러 라운드의 네이밍
브레인스토밍(연령·성별 캐릭터 → "체"(말투 접미사) → 인터넷 밈 조합 → 순우리
음식 은유)을 거쳐, "이모지 불맛/담백한 손맛/다정한 슴슴함/위트있는 칼칼함"으로
확정했다 — 음식 배달 서비스라는 정체성에 맞는 맛 은유이면서, 앞에 붙은
수식어(이모지/담백한/다정한/위트있는)가 실제 동작을 한눈에 알 수 있게 한다.

동시에, 직전에 배포한 리뷰 답글 카드 재설계(직접 쓰기/AI 추천 분리)의 최종
리뷰에서 발견된 미해결 버그도 이번 사이클에서 같이 처리하기로 했다: "직접
답글 쓰기" 모드로 타이핑하는 중에 필터/날짜/브랜드 드롭다운을 바꾸면 리스트가
새로고침되면서 그 리뷰 카드가 결과에서 빠져 언마운트되고, 저장하지 않은
텍스트가 그대로 사라진다(AI 추천 모드는 서버에 `ai_draft`가 이미 저장돼 있어
안전하지만, 직접 쓰기는 로컬 상태뿐이라 진짜 유실이 난다).

## 목표 / 비목표

**목표**
- 불만 카테고리 리뷰에서 페르소나 선택이 실제로 답글의 톤(이모지 사용량,
  격식 수준, 유머 여부)에 반영되게 한다 — 단, 원인 설명·사과 내용의
  실질적인 근거(그라운딩)는 계속 사장님의 학습된 말투(`store_style_profile`)와
  진짜 골든 예시에서만 가져온다. 페르소나는 "표면적 톤"만 조절하는 얇은
  레이어다.
- 위생/안전 민감 사안(`is_sensitive`)이거나 별점-내용 불일치
  (`sentiment_conflict`)인 리뷰는 페르소나 선택과 무관하게 이모지 없이
  진중한 톤으로 강제 전환한다.
- 4개 페르소나의 이름·설명을 세일즈랩에서 유래하지 않은 우리만의 이름으로
  교체한다: 이모지 불맛 / 담백한 손맛 / 다정한 슴슴함 / 위트있는 칼칼함.
- "직접 답글 쓰기" 모드의 초안이 리스트 재조회로 카드가 언마운트돼도
  유실되지 않게 한다.

**비목표**
- 칭찬/무난 리뷰(`no_issue`)는 계속 무료 템플릿 치환 방식을 쓴다 — RAG로
  통합하지 않는다(비용 트레이드오프, 브레인스토밍에서 명시적으로 결정).
  `template_high/mid/low` 문구 자체는 이번에 바꾸지 않는다 — 페르소나
  이름표만 바뀌고 실제 답글 문구는 그대로다.
- 리뷰 이미지 첨부는 다루지 않는다(별도 스펙, 아직 실 계정 검증 전).
- 배민 리뷰 이미지, `brands` 테이블 등 앞서 분해한 다른 서브프로젝트는
  이 스펙 범위 밖이다.

## 아키텍처

### 1. `reply_styles` 스키마 변경

```sql
ALTER TABLE reply_styles ADD COLUMN tone_instruction TEXT NOT NULL DEFAULT '';
```

기존 4개 행의 `name`/`description`/`tone_instruction`을 아래로 갱신한다
(운영 DB는 `UPDATE`, `seed.sql`은 `INSERT` 값 자체를 수정 — 새 환경에서
seed.sql을 재실행해도 같은 값이 나오게).

| name | description | tone_instruction |
|---|---|---|
| 이모지 불맛 | 이모지를 아낌없이 써서 발랄하고 신나게 답변합니다. | 이모지를 문장마다 적극적으로 사용하고, 밝고 통통 튀는 말투로 작성하세요. |
| 담백한 손맛 | 이모지 없이 꾸밈없고 진중하게, 책임감 있는 말투로 답변합니다. | 이모지를 쓰지 않고, 격식 있고 담백한 말투로 신뢰감 있게 작성하세요. |
| 다정한 슴슴함 | 이모지를 적당히 섞어 자극적이지 않고 편안하게, 다정한 말투로 답변합니다. | 이모지를 한두 개만 은은하게 섞어, 편안하고 다정한 말투로 작성하세요. |
| 위트있는 칼칼함 | 평소엔 재치있고 유쾌하지만, 불만 리뷰에는 장난기를 빼고 진지하게 답변합니다. | 가볍고 재치있는 표현을 섞되 과하지 않게, 위트 있는 말투로 작성하세요. |

`template_high`/`template_mid`/`template_low`는 그대로 둔다 — `name`이
바뀌어도 그 컬럼이 참조하는 실제 답글 문구는 이전과 동일하다(칭찬 리뷰
경로는 비목표이므로 안 건드림).

`tone_instruction`을 `NOT NULL DEFAULT ''`로 잡는 이유: 마이그레이션 시점에
기존 행이 있어도 컬럼 추가가 실패하지 않게 하기 위함이고, 곧바로 이어지는
`UPDATE`로 실제 값을 채운다 — 최종 상태에서 빈 문자열로 남는 행은 없다.

### 2. RAG 생성에 톤 레이어 주입

`backend/app/llm/generate.py`:

```python
_SENSITIVE_TONE_OVERRIDE = (
    "위생/안전 문제이거나 별점과 내용이 어긋나는 민감한 리뷰입니다. "
    "페르소나 톤과 무관하게 이모지 없이 차분하고 진중하게 작성하세요."
)


def _build_system_prompt(store: Store, style_rules: str, examples, tone_instruction: str) -> str:
    ...
    return f"""너는 "{store.name}"의 사장님을 대신해 배달앱 리뷰에 답글을 쓴다.

[이 가게의 답글 스타일]
{style_rules}

[답글 톤]
{tone_instruction}

[참고 예시 — 스타일 참고 전용]
...(기존 그대로)"""


def generate_ai_reply(db: Session, review: Review, store: Store, style: ReplyStyle) -> str:
    ...(기존 profile/examples/repeat_count/category_label 로직 그대로)
    tone_instruction = (
        _SENSITIVE_TONE_OVERRIDE
        if review.is_sensitive or review.sentiment_conflict
        else style.tone_instruction
    )
    system_prompt = _build_system_prompt(store, style_rules, examples, tone_instruction)
    user_message = _build_user_message(review, category_label, repeat_count)
    return client.call_sonnet(system_prompt, user_message, max_tokens=800)
```

`_build_user_message`의 기존 `is_sensitive` 분기(원인 추정 자제, 구체적
조치 제시 지시)는 건드리지 않는다 — 그건 답글 **내용**에 대한 지시고, 새
`tone_instruction`은 **표면적 톤**(이모지/격식)만 다루는 별개 레이어라
서로 보완적이다.

`[이 가게의 답글 스타일]`(그라운딩, `store_style_profile`)과 `[참고 예시]`
(골든 예시)는 페르소나·민감도와 무관하게 항상 그대로 유지된다 — 톤 레이어는
그 위에 얹히는 지시일 뿐, 원인 설명이나 사과의 실질적 근거를 대체하지
않는다.

### 3. 라우터: `style` 전달 + `tone_overridden` 응답 플래그

`backend/app/routers/reviews.py`의 `generate_reply`:

```python
    tone_overridden = False
    if review.category == "no_issue":
        template = {"low": style.template_low, "mid": style.template_mid, "high": style.template_high}[_band(review.rating)]
        content = _fill_template(template, review, review.store)
    else:
        try:
            content = generate_ai_reply(db, review, review.store, style)
        except Exception:
            raise HTTPException(
                503,
                detail={"message": "AI 답글 생성에 실패했어요. 잠시 후 다시 시도해주세요.", "error_code": "ai_generation_failed"},
            )
        tone_overridden = review.is_sensitive or review.sentiment_conflict

    draft = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=style.id,
        content=content, created_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    if review.status == "unanswered":
        review.status = "pending"
    db.commit()
    return {"content": content, "style_id": style.id, "tone_overridden": tone_overridden}
```

`tone_overridden`은 칭찬 리뷰 경로에서는 항상 `false`다(그 경로엔 톤 레이어
개념이 없음).

### 4. 프론트엔드: 민감 리뷰 안내 + 직접 쓰기 초안 보존

`frontend/src/app/(app)/reviews/page.tsx`의 `ReviewCard`:

- `generate()`가 응답의 `tone_overridden`을 새 상태(`toneOverridden`)에
  저장한다. AI 추천 패널(`mode === "ai"`)에서 `toneOverridden`이 true면
  스타일 드롭다운 위에 "⚠ 민감한 리뷰라 자동으로 진중한 톤으로 작성돼요"
  안내를 보여준다(스타일 드롭다운 자체는 계속 조작 가능하게 둔다 — 다음
  "다시 생성" 때는 그 사이 리뷰 데이터가 안 바뀌는 한 여전히 override가
  적용되므로, 드롭다운을 잠글 필요는 없다).
- 직접 쓰기 초안 보존: `mode`/`draft`가 바뀔 때마다 `sessionStorage`에
  `review-draft-${review.id}` 키로 `{mode, draft}`를 저장한다(마운트 시
  `useState`의 초기값을 계산할 때 sessionStorage에 해당 키가 있으면 그
  값으로, 없으면 기존 로직대로 초기화). `save()` 성공 시와 `cancelDraft()`
  호출 시 그 키를 지운다. 이러면 필터/날짜/브랜드를 바꿔 카드가
  언마운트됐다가 다시 나타나거나, 페이지를 새로고침해도(같은 탭 세션 안에서)
  타이핑하던 내용이 복구된다.

## 테스트 계획

**백엔드(pytest)**
- `generate_ai_reply`: `style.tone_instruction`이 시스템 프롬프트에 그대로
  포함되는지(스타일별로 다른 문구가 들어가는지).
- `is_sensitive=true`인 리뷰는 `style.tone_instruction` 대신
  `_SENSITIVE_TONE_OVERRIDE`가 시스템 프롬프트에 들어가는지(스타일이
  무엇이든 동일 문구).
- `sentiment_conflict=true`인 리뷰도 동일하게 override되는지.
- 그라운딩(`style_rules`, 골든 예시 블록)은 override 여부와 무관하게 항상
  시스템 프롬프트에 포함되는지.
- `POST /reviews/{id}/generate-reply` 응답의 `tone_overridden`이
  `is_sensitive`/`sentiment_conflict`/`no_issue` 조합별로 올바른 값을
  반환하는지(3가지 케이스: 민감 아님, `is_sensitive`, `sentiment_conflict`).
- `no_issue` 리뷰는 `tone_overridden`이 항상 `false`인지.

**프론트엔드(자동화 테스트 없음, 코어/온보딩/카드 재설계 때와 동일한
제약 — `npm run build`/`npm run lint` + dev 서버 수동 확인)**
- AI 추천 패널에서 민감 리뷰일 때 "⚠" 안내가 뜨는지.
- 직접 쓰기 모드로 타이핑 → 필터를 바꿔 카드가 사라졌다 다시 나타나게
  만듦(예: "답글 대기" → "전체" → 다시 "답글 대기") → 타이핑한 내용이
  복구되는지.
- 직접 쓰기 모드로 타이핑 → 페이지 새로고침 → 내용이 복구되는지.
- "이대로 답글 등록" 성공 후 같은 리뷰 카드에 다시 진입해도(다른 리뷰
  답글 등록으로 목록이 새로고침된 경우) 예전 초안이 되살아나지 않는지
  (sessionStorage 키가 저장 성공 시 지워졌는지 확인).
