# LLM + RAG 답글 온보딩 — 데이터 부트스트랩 설계

## 배경

`2026-08-21-llm-rag-reply-design.md`(코어 설계)로 실제 Claude API 기반
RAG 답글 생성이 이미 배포됐다. 하지만 `golden_examples`(few-shot 소스)에
는 브레인스토밍 중 확인한 진짜 부정 리뷰 답글 5건(전부 배달 지연·맛
관련)만 백필돼 있고, 위생·응대·가격·오배송 같은 카테고리는 진짜 예시가
0건이다. 이 상태로는 그 카테고리의 문제 리뷰가 와도 RAG가 참고할 게
없어 스타일 프로파일 기본 안내문에만 의존한다.

코어 설계 문서에서 이미 "AI 복제 증강 금지"(진짜 데이터를 AI로 뻥튀기
하면 정보량은 그대로고 편향만 증폭 — model collapse와 같은 원리)를
확정했으므로, 데이터를 늘리는 유일한 합법적 방법은 **사장님에게서 진짜
데이터를 능동적으로 받는 것**이다. 이 문서는 그 온보딩 플로우를
설계한다.

브레인스토밍 중 사용자가 강조한 실제 제품 목표: "이제 막 시작한 가게,
아직 리뷰가 적은 사장님도 처음부터 개인화된 느낌을 받게 하고 싶다."
이 목표는 AI가 데이터를 스스로 불려서 푸는 게 아니라, 아래 세 가지
조합으로 푼다:

1. **범용 시드**: 특정 사장님 데이터를 뻥튀기한 게 아니라, 가게와
   무관하게 처음부터 큐레이션된 "무난하게 좋은" 답글 예시 — 진짜
   데이터가 없는 신규 가게의 즉시 폴백.
2. **가입 직후 빠른 마법사**: 6개 카테고리를 한 번에 물어봐서, 첫날부터
   진짜 개인화가 시작되게.
3. **트리클(하루 2~3개)**: 마법사를 건너뛰었거나 놓친 카테고리를 나중에
   자연스럽게 채움.

## 목표 / 비목표

**목표**
- `golden_examples`가 비어있는 카테고리마다 사장님의 진짜 답글을 능동적으로
  확보한다.
- 신규 가게도 가입 첫날부터 어느 정도 개인화된 답글을 받을 수 있게 한다
  (범용 시드로 즉시 폴백, 마법사로 빠르게 진짜 데이터 확보).
- 기존 `generate_ai_reply`/`golden_examples`/`store_style_profile`
  인프라를 그대로 재사용한다 — 새 생성 로직을 중복으로 만들지 않는다.

**비목표**
- `brands` 테이블(원산지·메뉴·영업시간 등 "사실" 정보) + 사실 기반
  답글 생성은 별도 브레인스토밍으로 분리한다 — 새로운 배민 스크래핑
  대상 또는 수동 입력 화면이 필요해 범위가 크다.
- 브랜드별(`brand_id`) 말투 분리는 하지 않는다 — 한 계정의 여러 브랜드
  답글을 실제로는 같은 사람(사장님)이 쓰므로, 말투는 계속
  `store_id` 단위로 유지한다(브랜드로 쪼개면 이미 희소한 데이터가 더
  희소해짐).
- 벡터 검색은 여전히 도입하지 않는다.

## 아키텍처

```
[커버리지 스캔] 카테고리별 golden_examples 중 is_manual=true 행이
                있는지 확인(6개 카테고리 전부)
        │
        ├─ 배민 실계정 연결 성공 직후 → [빠른 마법사]
        │     비어있는 카테고리 전부에 대해 시나리오를 한 번에 만들어
        │     보여줌(있으면 재사용, 없으면 새로 생성)
        │
        └─ 대시보드 진입 시(매일) → [트리클]
              그중 오늘 아직 안 보여준 시나리오를 최대 3개만 노출
        │
        ▼
   (아직 시나리오가 없는 카테고리라면) [가상 리뷰 생성] Haiku
        ▼
   [마중물 초안 생성] 기존 generate_ai_reply(db, review, store) 재사용
        — 가상 리뷰 내용을 담은 임시(미저장) Review 객체를 넘기면,
          지금 있는 골든 예시/스타일 프로파일을 그대로 활용해 초안이
          나온다. 이 경로도 "예시 내용 복사 금지" 안전장치가 그대로
          적용된다.
        ▼
   [사장님이 직접 고쳐서 제출]
        ▼
   golden_examples에 source="onboarding"으로 승격
   (기존 save_final_reply의 diff 승격 로직과 동일한 판정: 마중물 초안과
   다르면 승격, 그대로 복붙이면 승격 안 함)
        → 백그라운드로 스타일 프로파일 재생성(기존 refresh_store_style_profile_background 재사용)
        → 시나리오 status="answered"
```

**마법사와 트리클은 같은 `onboarding_scenarios` 데이터를 공유한다.**
마법사가 카테고리를 다 채우면 트리클엔 보여줄 게 없다. 마법사를
건너뛰거나 일부만 답하면, 트리클이 나머지를 나중에 채운다. 건너뛴
(`skipped`) 시나리오는 삭제되지 않고, 다음 커버리지 스캔에서 재사용된다
(같은 카테고리에 중복 시나리오를 새로 만들지 않는다).

## DB 스키마

`onboarding_scenarios`(신규):

```sql
CREATE TABLE onboarding_scenarios (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            BIGINT       NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category            VARCHAR(24)  NOT NULL,
    virtual_review_text TEXT         NOT NULL,
    draft_text          TEXT         NOT NULL,
    status              VARCHAR(10)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'answered', 'skipped')),
    shown_on            DATE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (store_id, category)
);
```

`UNIQUE (store_id, category)` — 한 매장당 카테고리별로 시나리오는
하나만 존재한다(재사용 원칙을 스키마 레벨에서 강제). `status='answered'`
가 되면 그 카테고리는 이미 `golden_examples`에 real 데이터가 생겼다는
뜻이라, 커버리지 스캔이 다시는 그 카테고리를 대상으로 삼지 않는다
(재생성 불필요 — 행은 이력으로 남겨둔다).

`golden_examples.source` CHECK에 `'onboarding'`이 이미 코어 설계에
포함돼 있으므로 스키마 변경 불필요.

## 커버리지 스캔 + 시나리오 조회/생성

```python
def get_or_create_scenario(db: Session, store: Store, category: str) -> OnboardingScenario:
    """카테고리 하나에 대해 기존 시나리오가 있으면 재사용하고(pending/
    skipped 상관없이), 없으면 새로 만든다. status='answered'인 시나리오는
    호출자가 애초에 대상 카테고리 목록에서 걸러내고 이 함수를 부르므로
    여기서는 고려하지 않는다."""
    existing = db.scalar(
        select(OnboardingScenario).where(
            OnboardingScenario.store_id == store.id,
            OnboardingScenario.category == category,
        )
    )
    if existing is not None:
        return existing

    virtual_review_text = generate_virtual_review(category)
    fake_review = Review(
        store_id=store.id, platform_id=0, menu_summary="",
        rating=_REPRESENTATIVE_RATING[category], content=virtual_review_text,
        customer_nickname="", customer_order_count=1, category=category,
        is_sensitive=(category == "hygiene"), created_at=datetime.now(timezone.utc),
    )
    draft_text = generate_ai_reply(db, fake_review, store)

    scenario = OnboardingScenario(
        store_id=store.id, category=category,
        virtual_review_text=virtual_review_text, draft_text=draft_text,
        status="pending", created_at=datetime.now(timezone.utc),
    )
    db.add(scenario)
    db.commit()
    return scenario


def find_uncovered_categories(db: Session, store_id: int) -> list[str]:
    covered = set(db.scalars(
        select(GoldenExample.category).where(
            GoldenExample.store_id == store_id,
            GoldenExample.is_manual.is_(True),
            GoldenExample.is_synthetic.is_(False),
        ).distinct()
    ).all())
    return [c for c in VALID_CATEGORIES if c != "no_issue" and c not in covered]
```

`fake_review`는 **DB에 저장하지 않는다**(`db.add()` 호출 없음) — 진짜
`reviews` 테이블을 가상 리뷰로 오염시키면 안 된다는 코어 설계 원칙을
그대로 지킨다. `generate_ai_reply`는 `Review` 객체의 속성만 읽으므로
세션에 없는 임시 객체를 넘겨도 정상 동작한다(단, 이 함수 내부가
`review.id`를 참조하지 않는다는 전제 — 실제로 `generate.py`는
`review.category`/`review.content`/`review.rating`/
`review.customer_order_count`/`review.is_sensitive`만 읽는다).

`_REPRESENTATIVE_RATING`은 카테고리별로 통상적인 별점을 매핑한 상수
(`food_quality`/`delivery`/`hygiene`/`service`/`missing_or_wrong_item`은
1~2점, `price`는 3점 정도) — 실제 별점이 아니라 프롬프트 컨텍스트용
플레이스홀더일 뿐이라 정밀할 필요 없다.

## 가상 리뷰 생성 (Haiku)

```python
_VIRTUAL_REVIEW_PROMPT_TEMPLATE = """너는 배달 음식점에 실제로 달릴 법한
고객 불만 리뷰를 하나 만든다. 아래 불만 유형에 해당하는, 자연스러운
한국어 리뷰를 1~3문장으로 작성하라. 특정 가게 이름이나 메뉴는 언급하지
말고, 일반적인 상황으로 써라.

불만 유형: {category_label}

리뷰 본문만 출력하고 다른 설명은 붙이지 마라."""


def generate_virtual_review(category: str) -> str:
    label = _CATEGORY_LABELS[category]
    return client.call_haiku(
        "너는 배달앱 리뷰 예시를 만드는 도구다.",
        _VIRTUAL_REVIEW_PROMPT_TEMPLATE.format(category_label=label),
        max_tokens=200,
    )
```

## API

- `POST /reply-onboarding/wizard` — 배민 연결 성공 직후 프론트가 호출.
  `find_uncovered_categories`로 대상 목록을 구하고, 각각
  `get_or_create_scenario`를 호출해 전부 반환한다(최대 6개). 이미 다
  커버돼 있으면 빈 배열.
- `GET /reply-onboarding/today` — 대시보드 진입 시 호출. 오늘 날짜로
  이미 `shown_on`이 찍힌 시나리오가 있으면 그것만 반환. 없으면
  `find_uncovered_categories` 중 최대 3개를 골라(이미 있는 시나리오
  재사용 또는 신규 생성) `shown_on`을 오늘로 갱신한 뒤 반환.
- `POST /reply-onboarding/scenarios/{id}/answer` — `{"content": "..."}`
  본문. 시나리오의 `draft_text`와 다르면(또는 항상 — 온보딩은 애초에
  사장님이 답하는 게 목적이므로 무조건 승격) `golden_examples`에
  `source="onboarding"`으로 승격 + 백그라운드 스타일 프로파일 재생성 +
  `status="answered"`.
- `POST /reply-onboarding/scenarios/{id}/skip` — `status="skipped"`로만
  변경(승격 없음). 다음 스캔에서 다시 대상이 될 수 있다.

네 엔드포인트 전부 기존 라우터 관례(`reviews.py`의 `save_final_reply`
등)와 동일하게, 조회한 `store`/`scenario`가 요청한 사용자 소유인지
확인(`store.user_id != user.id`면 404)한 뒤 처리한다.

## 범용 시드 세트 (마이그레이션 스크립트)

`backend/scripts/seed_synthetic_golden_examples.py` — 대화형 아님, 6개
카테고리에 미리 작성된(아래 초안, 사용자 확인 후 확정) 답글을
`is_synthetic=true, is_manual=false, source="synthetic"`로 INSERT한다.
매장별이 아니라 **`store_id`가 있는 모든 매장에 공통으로 한 번씩**
적용한다(신규 매장이 생길 때도 같은 스크립트를 재실행하면 됨,
`UNIQUE`는 없지만 이미 있으면 스킵하는 멱등 로직 포함).

**초안(카테고리별 1건, 검토 후 확정)** — `review_text`/`reply_text` 둘 다
필요하다(few-shot 프롬프트가 리뷰-답글 쌍으로 예시를 보여주므로):

| 카테고리 | review_text | reply_text |
|---|---|---|
| food_quality | "닭이 너무 퍽퍽하고 식어서 왔어요. 맛이 예전같지 않네요." | "안녕하세요, 소중한 리뷰 남겨주셔서 감사합니다. 말씀해주신 맛과 관련된 부분, 조리 과정을 다시 한번 꼼꼼히 점검하겠습니다. 기대하신 만큼 만족을 드리지 못해 죄송한 마음입니다. 다음에는 더 신경 써서 준비하겠습니다." |
| delivery | "주문한 지 1시간 넘게 걸려서 왔어요. 배달이 너무 늦습니다." | "안녕하세요, 배달 관련해서 불편을 드려 죄송합니다. 도착 시간과 포장 상태 모두 다시 한번 점검하고, 배달 파트너와도 상황을 공유하겠습니다. 소중한 시간 기다리시게 해드려 죄송하고, 앞으로 더 신경 쓰겠습니다." |
| hygiene | "포장에서 이상한 냄새가 나고 위생 상태가 걱정되네요." | "안녕하세요, 이런 불편을 드려 정말 죄송합니다. 말씀해주신 부분은 가볍게 넘기지 않고 바로 확인해서 원인을 찾아보겠습니다. 혹시 괜찮으시면 가게로 연락 한번 주시면 자세히 안내드리겠습니다. 다시 한번 죄송하고, 더 세심하게 신경 쓰겠습니다." |
| service | "전화로 문의했는데 응대가 너무 불친절했어요." | "안녕하세요, 응대 과정에서 불편을 드려 죄송합니다. 말씀해주신 내용 무겁게 받아들이고, 다시는 이런 일이 없도록 신경 쓰겠습니다. 소중한 의견 남겨주셔서 감사드리고, 더 나은 모습으로 찾아뵙겠습니다." |
| price | "양에 비해 가격이 좀 비싸다고 느껴져요." | "안녕하세요, 가격에 대해 아쉬운 마음 남겨주셔서 감사합니다. 저희도 재료와 품질을 유지하면서 최대한 합리적인 가격을 고민하고 있습니다. 말씀해주신 의견 참고해서 계속 더 나은 방법을 찾아보겠습니다." |
| missing_or_wrong_item | "주문한 메뉴가 아니라 다른 메뉴가 왔어요. 확인 좀 해주세요." | "안녕하세요, 주문하신 것과 다르게 받으셔서 많이 당황하셨겠습니다. 정말 죄송합니다. 포장 과정을 다시 한번 꼼꼼히 확인하도록 하겠습니다. 불편하신 부분 있으시면 가게로 연락 주시면 바로 도와드리겠습니다." |

## 프론트엔드

- **대시보드**: "오늘의 답글 훈련" 카드 — `GET /reply-onboarding/today`
  결과가 있으면 노출, 없으면(전부 커버됐거나 오늘 몫을 다 처리했으면)
  카드 자체가 안 보인다. 카드 클릭 시 가상 리뷰 + 마중물 초안을 보여주고,
  텍스트 영역에서 수정 후 "저장"(→ answer) 또는 "건너뛰기"(→ skip) 버튼.
- **가게 연결 화면**: 배민 로그인 성공 직후, `POST /reply-onboarding/wizard`
  결과가 비어있지 않으면 모달로 "답글 스타일 빠르게 설정하기" 마법사를
  띄운다 — 카테고리마다 위와 같은 수정/저장/건너뛰기 UI를 순서대로
  보여준다(6개 중 몇 개 남았는지 진행 표시). 마법사는 언제든 닫을 수
  있고(선택사항), 닫아도 남은 카테고리는 트리클 카드에서 나중에 다시
  나온다.

## 테스트 계획

- `find_uncovered_categories`: real golden_example이 있는 카테고리는
  제외되는지, `no_issue`는 애초에 대상이 아닌지.
- `get_or_create_scenario`: 기존 시나리오 재사용(중복 생성 안 함),
  `fake_review`가 실제로 DB에 저장되지 않는지(`reviews` 테이블 count
  불변 확인), `generate_ai_reply`가 monkeypatch된 상태에서 draft_text가
  그 반환값과 일치하는지.
- `POST /reply-onboarding/wizard`: 이미 다 커버된 매장은 빈 배열; 일부만
  커버된 매장은 나머지 카테고리만 반환.
- `GET /reply-onboarding/today`: 최대 3개 제한, 같은 날 두 번 호출하면
  같은 시나리오 반환(재배정 안 함).
- `POST /.../answer`: golden_examples 승격 + 백그라운드 스타일 프로파일
  재생성 트리거 + status 변경, 초안과 동일하게 제출해도 승격되는지
  (온보딩은 무조건 사장님이 확인한 것이므로 diff 비교 없이 항상 승격 —
  코어 설계의 `save_final_reply`와의 차이점을 명시).
- `POST /.../skip`: status만 변경, golden_examples 승격 없음, 다음
  스캔에서 다시 대상이 되는지.
- `seed_synthetic_golden_examples.py`: 멱등성(두 번 실행해도 중복 안 됨).
