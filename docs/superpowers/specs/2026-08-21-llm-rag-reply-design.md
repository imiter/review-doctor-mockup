# LLM + RAG 기반 답글 생성 고도화 설계

## 배경

CLAUDE.md의 실 SaaS 전환 로드맵 4번("LLM 기반 답글 자동생성 고도화, RAG
포함")을 실제로 구현한다. 지금까지 답글 생성(`POST
/reviews/{review_id}/generate-reply`)은 `reply_styles`(4개 페르소나)의
고정 템플릿에 `{nickname}`/`{menu}`/`{store}`를 문자열 치환하는 순수
Mock이었다 — 실제 AI 호출이 전혀 없었다. 이번 설계로 실제 Claude API
호출을 처음 도입한다(이 프로젝트의 "절대 금지" 목록 중 "실제 AI API 호출
금지" 원칙의 예외, 2026-08-21 승인 — 실비용 발생을 사용자가 인지하고
승인함).

### 제품 정체성

단순 "답글 생성 도구"가 아니라 **"부정 리뷰 위기관리 + 매장 진단
어시스턴트"**로 포지셔닝한다. 경쟁 서비스들이 멈추는 지점("톤을
고르세요")에서 한 걸음 더 들어가, 이 리뷰가 왜 위험한지, 이 고객이 왜
특별한지, 이 가게가 실제로 이런 상황에 어떻게 대응해왔는지까지 자동으로
반영한다.

### 실측 데이터 확인 (설계 전 필수 검증)

브레인스토밍 중 프로덕션 DB를 직접 조회해 아래를 확인했다(2026-08-21):

- `review_replies`에 `reply_type='final'` 700건이 있으나 전부
  `ai_draft` 없이 바로 `final`만 있다 — 이 앱에서 "AI 초안 → 사장님 수정"
  플로우를 거친 적이 한 번도 없기 때문에(이번에 처음 만드는 기능이므로)
  구조적으로 당연한 결과다.
- 이 700건은 seed.sql Mock이 **아니라**, 사장님이 실제로 사용 중인 별도
  AI 답글 도구 + 직접 작성한 결과가 배민 실계정 스크래핑
  (`extract_owner_reply()`, `backend/app/review_sync.py:322-328`)으로
  들어온 실데이터다.
- 별점 1~2점(부정) 리뷰 답글은 5건뿐이며, 사용자가 전부 본인이 직접
  작성했다고 확인했다. 그중 하나(review id 63, 별점 **5점**인데 내용은
  "정체모를 이물질" 우려)의 실제 답글은 구체적 원인 설명("겉불을 쎄게
  조리해서 양념등이... 눌러붙어서 탄것 같습니다") + 재방문 고객 인지("단골
  분이신데") + 실질적 보상 제안("다음에 서비스 많이 나가도록
  하겠습니다")을 전부 담고 있다 — 이 설계가 목표로 하는 차별점이 이미
  실제 데이터에 존재함을 확인했다.
- 미답변(`status != 'answered'`) 리뷰가 38건 있고, 그중 별점 3점 이하가
  최소 2건(id 273, 156) 남아 있다 — 온보딩 플로우의 실제 소재로 쓸 수
  있다.

이 확인 때문에 "적은 진짜 예시를 AI로 증강"하는 접근(메아리 증폭 —
편향만 증폭되고 정보량은 늘지 않음)을 명시적으로 배제하고, 대신 "사장님
손으로 새 데이터를 길어 올리는" 온보딩 플로우를 채택한다.

## 목표 / 비목표

**목표**
- 문제 리뷰(불만이 감지된 리뷰)는 이 가게의 실제 대응 사례(RAG)를
  반영해 생성한다.
- 벡터 검색 없이, 불만 유형(category) 필터만으로 이번 규모(수십~수백
  건)에서 충분히 동작하는 검색을 만든다.
- 민감 리뷰(위생·이물질 등)는 자동 감지해 사장님께 우선 알림을 띄운다.
- 별점만으로는 못 잡는 숨은 불만(별점-텍스트 불일치)을 감지한다.
- 반복되는 불만 유형을 사장님이 인지할 수 있게 인사이트로 보여준다.
- 데이터가 적은 지금, AI 복제가 아니라 사장님 온보딩으로 진짜 예시를
  능동적으로 늘린다.

**비목표**
- 긍정 리뷰(불만 신호 없음)의 4-페르소나 템플릿 경로는 이번에 건드리지
  않는다 — 그대로 유지.
- 벡터 DB(pgvector 등)는 v1에 도입하지 않는다. 아래 "벡터 도입 트리거"
  조건이 실제로 발생하기 전까지는 재검토하지 않는다.
- 답글 완전 자동 등록은 여전히 범위 밖이다(CLAUDE.md "절대 금지" —
  이번에 승인된 예외는 "AI API 호출"뿐, "자동 등록"이 아니다). 최종
  발행은 계속 사장님이 버튼을 누른다.
- 고객 단위 이력 결합(같은 닉네임의 과거 리뷰를 매장 전체에서 이어붙여
  이탈 감지)은 별도 서브프로젝트로 분리한다 — 이번 범위 밖.
- 리뷰 ↔ 매출/정산 데이터는 연결 키가 없어(CLAUDE.md 기존 제약) 결합하지
  않는다.

## 핵심 설계 철학

> RAG 검색은 임베딩 거리가 아니라 LLM이 분류한 category로 한다.
> 데이터는 AI로 복제하지 않고, 사장님에게서 새로 길어 올린다.
> 벡터 DB는 폐기가 아니라 조건부 예약이다.

### 왜 벡터 검색이 아닌가

진짜 사장님 답글이 현재 5건뿐이다. 5건에 대한 벡터 KNN은 "전부
가져오기"와 통계적으로 동일하다. 이미 category 분류가 벡터 검색의
"의미 그룹핑" 역할을 대신하고(예: "환불/배달지연" 계열은 Haiku가 같은
category로 묶는다), 리뷰는 원래 짧은 텍스트라 청킹·임베딩의 이득도
작다. v1은 SQL 필터로 구현하되, `golden_examples` 스키마는 나중에
`embedding vector` 컬럼을 얹을 수 있게 열어둔다(아래 스키마 참고).

**벡터 도입 재검토 트리거** (아래 중 하나라도 발생하면 재검토):
1. 골든 예시가 category당 수십 건 이상 축적됐다.
2. category로 안 잡히는 애매한 롱테일 리뷰가 늘어난다.
3. "과거 비슷한 리뷰 찾기" 같은 본문 검색 기능이 별도로 필요해진다.

## 아키텍처

```
① 리뷰 동기화(review_sync.py, 기존 배민 스크래핑)
        │  신규 리뷰 INSERT 직후
        ▼
② [분류] Haiku 1회 호출
   → category, is_sensitive, sentiment_conflict를 reviews에 저장
        │
        ├─ is_sensitive=true ──────────────────→ alerts에 'sensitive_review' 즉시 생성
        │
        ▼
③ category='no_issue' ?
   ├─ 예 (불만 신호 없음) → 기존 4-페르소나 템플릿 경로 (변경 없음)
   └─ 아니오 (문제 리뷰) → 아래 RAG 파이프라인
        │
        ▼
④ [검색] SQL 필터 (벡터 없음)
   golden_examples WHERE category=? AND is_manual=true AND is_synthetic=false
   ORDER BY created_at DESC LIMIT 3
   (부족하면 is_synthetic=true 예시로 보충)
   + store_style_profile.rules 조회
   + customer_order_count(재방문 여부, 직접 필드 — 분류 불필요)
   + 같은 category 최근 N일 반복 건수(조회 시점 계산, 정규화 원칙)
        │
        ▼
⑤ [생성] Sonnet — "스타일만 참고, 사건 내용 복사 금지" 지시 포함
        │
        ▼
   AI 초안(review_replies, reply_type='ai_draft') 저장
        │
        ▼
   사장님이 검토/수정 → POST /reviews/{id}/reply 저장
        │
        ▼
⑥ 초안과 최종본이 다르면(또는 초안 없이 직접 작성) → golden_examples에
   is_manual=true, is_synthetic=false로 신규 등록
        │
        ▼
   store_style_profile 재생성(반드시 is_manual=true AND is_synthetic=false
   데이터로만 — 순환 오염 방지)
```

## DB 스키마 변경

### `reviews` 확장

```sql
ALTER TABLE reviews
    ADD COLUMN category           VARCHAR(24) NOT NULL DEFAULT 'no_issue'
        CHECK (category IN (
            'food_quality', 'delivery', 'hygiene', 'service',
            'price', 'missing_or_wrong_item', 'no_issue'
        )),
    ADD COLUMN is_sensitive       BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN sentiment_conflict BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX idx_reviews_category ON reviews(store_id, category);
```

`DEFAULT 'no_issue'`인 이유: 분류는 신규 리뷰가 들어올 때(②)만 실행하고
기존 700여 건은 소급 분류하지 않는다(비용·범위 통제) — 이미 답글이 달린
과거 리뷰는 재생성 대상이 아니므로 분류가 없어도 실질적 문제가 없다.
필요하면 별도 백필 스크립트로 나중에 처리한다.

### `golden_examples` (신규 — few-shot 소스)

```sql
CREATE TABLE golden_examples (
    id              BIGSERIAL PRIMARY KEY,
    store_id        BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category        VARCHAR(24)  NOT NULL,
    review_text     TEXT         NOT NULL,
    reply_text      TEXT         NOT NULL,
    is_manual       BOOLEAN      NOT NULL,  -- 사장님이 직접 쓰거나 수정 = true
    is_synthetic    BOOLEAN      NOT NULL,  -- 순수 AI 생성 모범답안 = true
    source          VARCHAR(16)  NOT NULL
        CHECK (source IN ('backfill', 'organic', 'onboarding', 'synthetic')),
    source_review_id BIGINT      REFERENCES reviews(id) ON DELETE SET NULL,
    source_reply_id  BIGINT      REFERENCES review_replies(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
    -- 나중에 벡터 도입 트리거가 발생하면 여기에
    -- embedding vector(1536) 컬럼을 추가하면 된다(pgvector 확장 필요).
);

CREATE INDEX idx_golden_examples_lookup
    ON golden_examples(store_id, category, is_manual, is_synthetic, created_at DESC);
```

`source_review_id`/`source_reply_id`는 디버깅용 추적 참조일 뿐, 검색
경로(④)는 절대 이 컬럼을 조인하지 않는다 — `review_text`/`reply_text`를
그대로 복사해 저장하므로 검색이 `golden_examples` 한 테이블만으로
끝난다(밀리초 내 응답, "왜 이 예시를 골랐는지 SQL로 100% 설명 가능"이라는
자가 점검을 만족).

**데이터 진짜/가짜 판정표**

| 데이터 | is_manual | is_synthetic | source | 스타일 프로파일 반영 |
|---|---|---|---|---|
| 브레인스토밍 중 확인한 기존 5건(부정 리뷰 실답글) | true | false | backfill | ✅ |
| 앱에서 사장님이 초안을 수정하거나 직접 쓴 답글 | true | false | organic | ✅ |
| 온보딩에서 사장님이 마중물 초안을 수정한 답글 | true | false | onboarding | ✅ |
| 순수 AI 생성 모범답안(예시 부족 시 보충용) | false | true | synthetic | ❌ |

### `store_style_profile` (신규 — 스타일 규칙 캐싱)

```sql
CREATE TABLE store_style_profile (
    store_id            BIGINT       PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
    rules               TEXT         NOT NULL,   -- LLM이 추출한 스타일 규칙 5~7줄
    generated_from_count INT         NOT NULL,   -- 몇 건의 진짜 답글로 뽑았는지
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

재생성 쿼리는 **반드시** `golden_examples WHERE is_manual = true AND
is_synthetic = false`로 제한한다 — 가상 데이터로 스타일을 추출하면 AI가
만든 톤이 기준이 되어 자기가 만든 걸 자기가 학습하는 순환 오염이
생긴다. 이 테이블 자체가 없으면(첫 답글 이전) 생성 단계(⑤)는 스타일
규칙 없이 진행하고, 시스템 프롬프트에 일반적인 정중한 사과문 원칙만
포함한다.

### `onboarding_scenarios` (신규 — 스타일 온보딩용)

```sql
CREATE TABLE onboarding_scenarios (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    virtual_review_text TEXT         NOT NULL,  -- AI가 만든 가상 부정 리뷰
    category            VARCHAR(24)  NOT NULL,
    draft_text          TEXT,                   -- 마중물 초안(일부러 완벽하지 않게)
    status              VARCHAR(10)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'answered', 'skipped')),
    shown_on            DATE,                    -- 하루 2~3개 페이싱용 (오늘 이미 보여줬는지)
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

`virtual_review_text`는 항상 가상(is_synthetic 대상)이지만, 사장님이
`status='answered'`로 만들며 남긴 답글은 진짜다(위 판정표의 onboarding
행) — 이 구분을 스키마가 아니라 golden_examples 승격 로직이 담당한다
(가상 리뷰 자체를 `reviews`에 절대 넣지 않는다 — 실제 리뷰 목록/통계를
오염시키면 안 되므로 완전히 별도 테이블로 격리했다).

### `alerts` CHECK 확장

```sql
ALTER TABLE alerts DROP CONSTRAINT alerts_alert_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_alert_type_check
    CHECK (alert_type IN ('negative_review', 'unanswered_review', 'rank_drop', 'sensitive_review'));
```

> **중요한 기존 상태 확인**: `alerts` 테이블은 현재 seed.sql이 한 번
> 채워 넣은 Mock 데이터만 있고, 이걸 동적으로 생성하는 코드가
> **어디에도 없다**(`Alert(` 생성 코드가 `backend/app/` 전체에 전무함,
> 2026-08-21 확인). 이번 기능이 이 프로젝트에서 처음으로 실제 알림을
> 동적 생성하는 코드가 된다. 기존 `negative_review`/`unanswered_review`
> 타입은 이번에 건드리지 않는다(여전히 Mock) — 새로 추가하는
> `sensitive_review` 타입만 실제로 동적 생성한다. 두 종류를 동시에
> 실동작시키는 건 별도 스코프로 분리한다.

## 분류 단계 (Haiku)

**시점**: `backend/app/review_sync.py`의 리뷰 INSERT 직후(현재
228-330줄, 특히 316-330줄 — `db.add(review)` 다음). 이 시점에 분류해야
사장님이 리뷰를 열어보지 않아도 민감 리뷰 알림이 뜬다("답글 생성" 클릭
시점까지 미루면 위기관리 기능의 의미가 없어진다).

**함수**: `backend/app/llm/classify.py`(신규)

```python
def classify_review(content: str, rating: int) -> ReviewClassification:
    """Haiku 1회 호출로 category/is_sensitive/sentiment_conflict를 함께
    뽑는다 — 세 판단이 서로 연관돼 있어(예: 위생 문제는 거의 항상
    is_sensitive) 한 번의 호출로 같이 받는 게 자연스럽고 비용도 아낀다."""
```

**분류 시스템 프롬프트 초안**:

```
너는 배달 음식점 리뷰를 분석하는 분류기다. 아래 리뷰를 읽고 JSON으로만 답하라.

카테고리(정확히 하나만 선택):
- food_quality: 맛, 온도, 양에 대한 불만
- delivery: 배달 지연, 파손에 대한 불만
- hygiene: 위생, 이물질, 곰팡이 등 안전 관련 불만
- service: 응대, 태도에 대한 불만
- price: 가격에 대한 불만
- missing_or_wrong_item: 누락, 오배송
- no_issue: 위 어디에도 해당하는 불만이 없음 (칭찬만 있거나 중립적)

is_sensitive: 위생/이물질/알레르기/안전 관련 언급이 있어 신중한 대응이
필요하면 true. 단순 맛 불만 등은 false.

sentiment_conflict: 별점(rating)과 리뷰 내용의 감정이 서로 어긋나면
true. 예: 별점은 4~5점인데 내용에 뚜렷한 불만이 섞여 있는 경우.
별점이 낮은데 내용도 부정적인 건 "일치"이므로 false.

리뷰: "{content}"
별점: {rating}

JSON 형식: {"category": "...", "is_sensitive": true/false, "sentiment_conflict": true/false}
```

**민감 리뷰 알림**: `is_sensitive=true`면 즉시
`Alert(store_id=review.store_id, alert_type="sensitive_review",
message=f"민감한 리뷰가 감지됐습니다: {review.menu_summary} 관련 — 우선
확인이 필요합니다")`를 만든다.

## 검색 + 생성 단계

**시점**: 기존 `POST /reviews/{review_id}/generate-reply`
(`backend/app/routers/reviews.py:108-141`)를 분기한다.

```python
@router.post("/reviews/{review_id}/generate-reply")
def generate_reply(...):
    ...
    if review.category == "no_issue":
        # 기존 템플릿 경로 — 완전히 그대로 유지
        template = {...}[_band(review.rating)]
        content = _fill_template(template, review, review.store)
    else:
        content = generate_ai_reply(review, review.store, db)  # 신규 RAG 경로
    ...
```

**검색 쿼리** (`backend/app/llm/rag.py`, 신규):

```python
def fetch_golden_examples(db: Session, store_id: int, category: str, limit: int = 3) -> list[GoldenExample]:
    real = db.scalars(
        select(GoldenExample)
        .where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        )
        .order_by(GoldenExample.created_at.desc())
        .limit(limit)
    ).all()
    if len(real) >= limit:
        return real
    synthetic = db.scalars(
        select(GoldenExample)
        .where(
            GoldenExample.store_id == store_id,
            GoldenExample.category == category,
            GoldenExample.is_synthetic.is_(True),
        )
        .order_by(GoldenExample.created_at.desc())
        .limit(limit - len(real))
    ).all()
    return real + synthetic
```

**반복 이슈 카운트** (정규화 원칙 — 저장하지 않고 조회 시점 계산):

```python
def count_recent_same_category(db: Session, store_id: int, category: str, days: int = 30) -> int:
    return db.scalar(
        select(func.count()).select_from(Review).where(
            Review.store_id == store_id, Review.category == category,
            Review.created_at >= datetime.now(timezone.utc) - timedelta(days=days),
        )
    )
```

**생성 시스템 프롬프트 초안**:

```
너는 "{store_name}"의 사장님을 대신해 배달앱 리뷰에 답글을 쓴다.

[이 가게의 답글 스타일]
{store_style_profile.rules 또는 "아직 학습된 스타일이 없습니다. 정중하고
진솔한 사과문 원칙을 따르세요."}

[참고 예시 — 스타일 참고 전용]
아래는 이 가게 사장님이 실제로 쓴(또는 승인한) 답글 예시다.
**절대 지켜야 할 규칙**: 이 예시들은 말투·태도·구조(원인 설명 → 사과 →
재방문 유도)만 참고하라. 문장 내용이나 구체적 원인을 그대로 복사하지
마라. 반드시 "이번 리뷰의 실제 상황"에만 근거해 새로 작성하라.

{예시 1: 리뷰 "...", 답글 "..."}
{예시 2: ...}

[이번 리뷰]
별점: {rating}
불만 유형: {category}
내용: "{content}"
이 고객의 누적 주문 횟수: {customer_order_count}회
{customer_order_count > 1 이면: "재방문 고객이니 자연스럽게 반영하세요."}
{count_recent_same_category > 1이면: f"이 유형 불만이 최근 30일간
{count}건째입니다 — 반복 문제임을 인지하되 변명처럼 들리지 않게 주의하세요."}
{is_sensitive이면: "위생/안전 관련 민감 사안입니다. 섣부른 원인 추정이나
과도한 변명 없이, 진지하게 사과하고 구체적 조치(연락처 안내 등)를
제시하세요."}

위 내용을 바탕으로 답글을 작성하라. 예시의 사건을 재활용하지 않았는지
스스로 점검한 뒤 최종 답글만 출력하라.
```

## 온보딩 플로우 (데이터 부트스트랩)

**목표**: AI 복제가 아니라 사장님에게서 새 데이터를 능동적으로 길어
올린다.

```
① category 커버리지 스캔 → golden_examples가 비어 있는 category 추출
② 빈 category마다 현실적인 가상 부정 리뷰 생성 (Haiku)
   → onboarding_scenarios에 저장 (virtual_review_text, category)
③ 마중물 초안 생성 (Sonnet, 일부러 완벽하지 않게) → draft_text에 저장
④ 사장님에게 "오늘의 답글 훈련" 카드로 하루 2~3개씩만 노출.
   별도 스케줄러 없이 조회 시점 lazy 배정 방식이다(`effective_plan()`과
   같은 패턴) — `GET /reply-training/today` 호출 시, 오늘 날짜로 이미
   `shown_on`이 찍힌 행이 있으면 그걸 반환하고, 없으면 아직 `pending`인
   시나리오 중 최대 3개(가능하면 서로 다른 category 우선)를 골라
   `shown_on`을 오늘로 갱신한 뒤 반환한다.
⑤ 사장님이 자기 말투로 수정 → status='answered', 수정본 저장
⑥ golden_examples에 is_manual=true, is_synthetic=false, source='onboarding'으로 승격
⑦ store_style_profile 재생성 트리거
```

`draft_text`를 일부러 완벽하지 않게 만드는 이유: 초안이 이미 훌륭하면
사장님이 그냥 저장 버튼만 누르고 넘어가 버려 실제 편집(진짜 목소리
추출)이 일어나지 않는다 — 의도적으로 수정을 유도한다.

**백필**: 이 설계 문서 작성 중 확인한 기존 5건(부정 리뷰 실답글)은
onboarding을 거치지 않고 `source='backfill'`로 즉시
`golden_examples`에 넣는다(별도 일회성 스크립트, 사장님 추가 조작
불필요 — 이미 실제로 작성하신 데이터이므로).

## 스타일 프로파일 재생성

`golden_examples`에 새 행이 `is_manual=true AND is_synthetic=false`로
추가될 때마다(⑥, 온보딩 ⑥) 백그라운드 태스크로 트리거한다:

```python
def refresh_store_style_profile(store_id: int) -> None:
    examples = db.scalars(
        select(GoldenExample).where(
            GoldenExample.store_id == store_id,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        )
    ).all()
    # Sonnet 1회 호출: 이 예시들을 읽고 "스타일 규칙 5~7줄" 추출
    rules = extract_style_rules(examples)
    upsert(StoreStyleProfile(store_id=store_id, rules=rules,
                               generated_from_count=len(examples)))
```

## 모델/API 설정

- **분류**: `claude-haiku-4-5-20251001` (저비용)
- **답글 생성 + 스타일 규칙 추출**: `claude-sonnet-5` (한국어 사과문의
  정중함·미묘한 감정 표현에 강함)
- `ANTHROPIC_API_KEY` 신규 환경변수 (Railway에 배포 시 설정 — Claude
  Pro 구독과는 별도 과금, console.anthropic.com에서 발급)
- `requirements.txt`에 `anthropic` SDK 추가 — 이번이 이 프로젝트에서
  처음 추가하는 실제 외부 API 의존성이다.

## 프론트엔드 변경

- **리뷰 관리 화면**: `category != 'no_issue'`인 리뷰 카드에 진단
  배지(불만 유형, 재방문 여부, 반복 건수) 노출. `is_sensitive`면 강조
  표시.
- **대시보드**: 기존 `GET /alerts` 응답에 `sensitive_review` 타입이
  섞여 나오므로, 프론트가 이미 있는 알림 리스트 UI로 자연스럽게
  표시된다(새 컴포넌트 불필요, 문구만 처리).
- **온보딩 카드**: "오늘의 답글 훈련" — 가게 연결 화면이나 대시보드에
  작게 노출, 하루 최대 3개.

## 테스트 계획

- `classify_review`: Anthropic API를 monkeypatch해서 프롬프트 구성과
  JSON 파싱을 검증(실제 API 호출 없이).
- `fetch_golden_examples`: real 3건 이상/부족/0건 시 synthetic 보충
  로직을 실제 DB 행으로 검증.
- `count_recent_same_category`: 순수 카운트 쿼리, 경계값(정확히 N일
  전) 테스트.
- `generate_reply` 라우터: `category='no_issue'`면 기존 템플릿 경로를
  타는지(회귀), 아니면 신규 경로가 호출되는지 monkeypatch로 분기 검증.
- golden_examples 승격 로직: 초안=최종본(변화 없음)이면 승격 안 됨,
  다르면 승격됨, 초안 없이 직접 작성해도 승격됨 — 세 케이스 모두 검증.
- 온보딩: 커버리지 스캔이 실제로 빈 category만 추출하는지, 하루 노출
  개수 상한이 지켜지는지.

## 향후(비목표 재확인)

- 고객 이력 결합(이탈 감지)은 별도 브레인스토밍.
- 벡터 검색은 위 트리거 조건 발생 시 재검토.
- `negative_review`/`unanswered_review` 알림의 실동작화는 별도 스코프.
