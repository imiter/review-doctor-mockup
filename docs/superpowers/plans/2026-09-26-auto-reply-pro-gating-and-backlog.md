# 자동 답글 Pro 전용화 + 기존 미답변 리뷰 소급 처리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자동 답글(신규 리뷰 즉시 응답 + 기존 미답변 리뷰 소급 처리) 기능
전체를 Pro 전용으로 잠그고, 이미 켜둔 뒤에도 소급 적용 안 되던 미답변 리뷰를
매 동기화 때 함께 처리하도록 확장한다.

**Architecture:** 백엔드 두 지점에서 게이팅한다 — 설정 저장 시점
(`PUT /reply-settings`)과 실행 시점(`review_sync.py`의 `_run_sync`, 진짜
비용/쓰기가 발생하는 최종 관문). 소급 처리는 `_run_sync`의 매장(shop_no)
루프 안에 새 쿼리+루프를 추가하는 방식으로, 신규 리뷰 처리와 동일한
생성·제출·상태변경 로직을 재사용한다. 프론트는 `/reviews/rules` 페이지
전체를 기존 `/ads` 페이지와 동일한 Pro 잠금 패턴으로 가린다.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, Next.js/React, TypeScript.

## Global Constraints

- 별점 자동답글 하한은 항상 5점 고정(`_AUTO_REPLY_MIN_RATING_FLOOR`, 하드코딩) —
  `reply_settings.auto_reply_min_rating` 값과 무관, 이번 작업에서도 그대로 유지.
- 자동답글 대상 조건(신규/소급 공통): `rating >= 5 AND category == "no_issue"
  AND NOT is_sensitive AND NOT sentiment_conflict`.
- 소급 처리는 이미 배민에 사장님이 직접 답글을 달았을 가능성을 실시간
  재확인하지 않는다 — DB `status='unanswered'`를 그대로 신뢰(설계 문서 결정
  사항 3번). 새 안전장치를 추가하지 않는다.
- 소급 처리는 한 번에 개수 제한 없이 전부 처리한다(설계 문서 결정 사항 2번).
- golden_examples 승격은 신규/소급 모두 하지 않는다(기존 정책 유지, 변경 없음).
- 참고 스펙: `docs/superpowers/specs/2026-09-26-auto-reply-pro-gating-and-backlog-design.md`

---

### Task 1: `PUT /reply-settings` Pro 게이트 + 오래된 docstring 정리

**Files:**
- Modify: `backend/app/routers/reply_settings.py`
- Test: `backend/tests/test_reply_settings.py`

**Interfaces:**
- Consumes: `app.plan.effective_plan(sub: Subscription | None) -> str`,
  `app.models.Subscription`(기존 모델, 필드: `user_id`, `plan`, `expires_at`).
- Produces: 없음(라우트 계층 변경, 다른 태스크가 이 코드를 직접 참조하지 않음).

- [ ] **Step 1: 실패하는 테스트 작성 — Basic이 자동답글을 켜려 하면 403**

`backend/tests/test_reply_settings.py`에 추가:

```python
def test_update_reply_settings_rejects_auto_reply_for_basic(client, db_session, seeded_user, reply_styles, auth_headers):
    make_settings(db_session, seeded_user["store"], reply_styles)  # 기본 Subscription은 basic (conftest.seeded_user)
    res = client.put("/reply-settings", json={"auto_reply_enabled": True}, headers=auth_headers)
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "pro_required"


def test_update_reply_settings_allows_auto_reply_for_pro(client, db_session, seeded_user, reply_styles, auth_headers):
    from app.models import Subscription
    from datetime import date
    db_session.query(Subscription).filter_by(user_id=seeded_user["user"].id).update(
        {"plan": "pro", "expires_at": date(2099, 1, 1)}
    )
    db_session.commit()
    make_settings(db_session, seeded_user["store"], reply_styles)
    res = client.put("/reply-settings", json={"auto_reply_enabled": True}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["auto_reply_enabled"] is True


def test_update_reply_settings_allows_disabling_for_basic(client, db_session, seeded_user, reply_styles, auth_headers):
    """끄는 요청은 플랜과 무관하게 항상 허용돼야 한다 — Pro였다가
    다운그레이드된 사용자도 자기 설정을 끌 수는 있어야 한다."""
    make_settings(db_session, seeded_user["store"], reply_styles, auto_reply_enabled=True)
    res = client.put("/reply-settings", json={"auto_reply_enabled": False}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["auto_reply_enabled"] is False
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_reply_settings.py -v`
Expected: `test_update_reply_settings_rejects_auto_reply_for_basic`이 FAIL(현재는
200을 돌려줌 — 게이트가 아직 없음). 나머지 두 개는 이미 통과할 수 있음(문제
없음, 3번째는 기존 동작과 같아서).

- [ ] **Step 3: 게이트 구현 + 오래된 docstring 정리**

`backend/app/routers/reply_settings.py` 수정. 상단 import에 추가:

```python
from app.models import ReplySetting, Subscription, User
from app.plan import effective_plan
```

(`ReplySetting, User`는 기존 import에서 이미 있음 — `Subscription` 추가.)

파일 최상단 docstring에서 이 문장 삭제:

```
자동 답글은 Mock이다 — auto_reply_enabled를 켜도 실제로 답글이 자동 등록되지 않는다.
```

`update_reply_settings` 함수 안, `if body.auto_reply_min_rating is not None...`
검증 블록 바로 다음에 추가:

```python
    if body.auto_reply_enabled:
        sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
        if effective_plan(sub) != "pro":
            raise HTTPException(
                403,
                detail={"message": "자동 답글은 Pro 플랜 전용 기능입니다.", "error_code": "pro_required"},
            )
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_reply_settings.py -v`
Expected: 전체 PASS (기존 5개 + 신규 3개 = 8개).

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/reply_settings.py backend/tests/test_reply_settings.py
git commit -m "feat: 자동 답글 설정을 Pro 전용으로 잠금 (PUT /reply-settings)"
```

---

### Task 2: `review_sync.py` 실행 시점 Pro 게이트

**Files:**
- Modify: `backend/app/review_sync.py:1-34` (import), `backend/app/review_sync.py:339-344` (게이트 로직)
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: `app.plan.effective_plan`, `app.models.Subscription`.
- Produces: `_run_sync` 함수 스코프 안의 지역 변수 `is_pro: bool` — Task 3이
  직접 참조하진 않지만(Task 3의 소급 루프는 `auto_reply_style is not None`
  조건만 재사용), 같은 위치에서 함께 계산되므로 존재를 알아둘 것.

- [ ] **Step 1: 실패하는 테스트 작성 — Basic 매장은 조건을 만족해도 자동답글 안 탐**

**주의**: 아래 테스트는 `_enable_auto_reply` 헬퍼를 쓰지 않는다 — Step 2에서
그 헬퍼 자체를 "호출하면 항상 Pro로도 업그레이드"하도록 고칠 예정이라,
이 테스트(Basic 상태를 유지해야 함)에서 그 헬퍼를 쓰면 검증하려는 조건이
사라진다. 대신 `ReplySetting`을 직접 만든다(seeded_user 기본 Subscription은
이미 basic이라 별도 다운그레이드 불필요).

`backend/tests/test_review_sync.py`에 추가(기존 `_enable_auto_reply` 헬퍼
바로 아래, 다른 자동답글 테스트들 근처):

```python
def test_sync_does_not_auto_reply_when_not_pro(db_session, sync_setup, reply_styles, monkeypatch):
    """reply_settings.auto_reply_enabled=True인데 그 가게 주인이 Pro가
    아니면(예: Pro였다가 다운그레이드) 자동답글이 실행되면 안 된다."""
    import app.review_sync as review_sync_mod
    from app.llm.classify import ReviewClassification
    from app.models import ReplySetting, Review

    job, conn = sync_setup
    db_session.add(ReplySetting(
        store_id=job.store_id, style_id=reply_styles.id, promo_text="", include_nickname=True,
        include_menu=True, include_store_name=True, promo_on_negative=False,
        auto_reply_enabled=True, auto_reply_min_rating=1,
    ))
    db_session.commit()
    # seeded_user 기본 Subscription은 basic이라 별도 다운그레이드 불필요.

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [_RAW_1])  # rating 5.0
    monkeypatch.setattr(
        review_sync_mod, "classify_review",
        lambda content, rating: ReviewClassification(category="no_issue", is_sensitive=False, sentiment_conflict=False),
    )
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: pytest.fail("should not be called"))
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: pytest.fail("should not be called"))

    sync_reviews_for_job(job, conn, db_session)

    review = db_session.query(Review).filter_by(external_review_id=_RAW_1["id"]).one()
    assert review.status == "unanswered"
```

- [ ] **Step 2: `_enable_auto_reply` 헬퍼를 고쳐서 호출하는 가게를 Pro로도 업그레이드**

이걸 먼저 해야 하는 이유: 이 헬퍼를 쓰는 **기존** 자동답글 테스트 8개는
전부 seeded_user 기본값(Basic)으로 돌고 있다 — Step 4에서 게이트를 넣으면
이 8개가 전부 깨진다. 헬퍼 자체가 호출한 가게를 Pro로도 올리게 고치면,
8개 전부 코드 변경 없이 계속 같은 의도로 통과한다.

`backend/tests/test_review_sync.py`의 `_enable_auto_reply` 함수를 찾아서
(2340번째 줄 근처) 아래처럼 바꾼다 — 이렇게 하면 이 헬퍼를 쓰는 기존 테스트
8개가 전부 자동으로 Pro 상태가 되어 이번 게이트 추가 후에도 계속 같은
의미로 통과한다:

```python
def _enable_auto_reply(db_session, store_id, style_id):
    from app.models import ReplySetting, Store, Subscription
    from datetime import date

    db_session.add(ReplySetting(
        store_id=store_id, style_id=style_id, promo_text="", include_nickname=True,
        include_menu=True, include_store_name=True, promo_on_negative=False,
        auto_reply_enabled=True, auto_reply_min_rating=1,
    ))
    store = db_session.get(Store, store_id)
    db_session.query(Subscription).filter_by(user_id=store.user_id).update(
        {"plan": "pro", "expires_at": date(2099, 1, 1)}
    )
    db_session.commit()
```

- [ ] **Step 3: 테스트 실행해서 새 테스트만 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "auto_reply" -v`
Expected: `test_sync_does_not_auto_reply_when_not_pro`만 FAIL(현재 게이트가
없어서 실제로 자동답글이 실행돼버림 → `pytest.fail("should not be called")`에
걸림). 기존 8개 테스트는 이미 이 시점에 PASS(Step 2에서 헬퍼를 고쳤으므로).

- [ ] **Step 4: 게이트 구현**

`backend/app/review_sync.py` 상단 import(17-33행 `from app.models import (...)`
블록)에 `Subscription` 추가:

```python
from app.models import (
    AdCampaign,
    Alert,
    BaeminShopBrand,
    BrandAdClickMetric,
    BrandMenuInfo,
    DailySettlement,
    Order,
    RepurchaseMetric,
    ReplySetting,
    ReplyStyle,
    Review,
    ReviewReply,
    ReviewSyncJob,
    Store,
    StorePlatformConnection,
    Subscription,
)
```

그 아래 어딘가(예: `from app.llm.classify import ...` 근처)에 추가:

```python
from app.plan import effective_plan
```

339~344행의 아래 블록을:

```python
    _AUTO_REPLY_MIN_RATING_FLOOR = 5
    reply_settings = db.scalar(select(ReplySetting).where(ReplySetting.store_id == job.store_id))
    auto_reply_style = None
    if reply_settings is not None and reply_settings.auto_reply_enabled:
        auto_reply_style = db.get(ReplyStyle, reply_settings.style_id)
    store = db.get(Store, job.store_id)
```

아래처럼 바꾼다(`store` 조회를 위로 올리고, `is_pro` 계산 추가, 조건에
`is_pro` 포함):

```python
    _AUTO_REPLY_MIN_RATING_FLOOR = 5
    store = db.get(Store, job.store_id)
    # 설정 저장 시점(PUT /reply-settings)에도 Pro가 아니면 auto_reply_enabled를
    # 켤 수 없게 막아뒀지만, Pro였다가 Basic으로 내려간 뒤에도 이미 켜둔 값이
    # DB에 남아있을 수 있다 — 실제 비용(Sonnet 호출)과 배민 계정 쓰기가
    # 발생하는 이 지점이 최종 관문이라 여기서도 다시 확인한다.
    sub = db.scalar(select(Subscription).where(Subscription.user_id == store.user_id))
    is_pro = effective_plan(sub) == "pro"
    reply_settings = db.scalar(select(ReplySetting).where(ReplySetting.store_id == job.store_id))
    auto_reply_style = None
    if reply_settings is not None and reply_settings.auto_reply_enabled and is_pro:
        auto_reply_style = db.get(ReplyStyle, reply_settings.style_id)
```

- [ ] **Step 5: 테스트 실행해서 전부 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "auto_reply" -v`
Expected: 전체 PASS(기존 8개 + 신규 1개 = 9개).

- [ ] **Step 6: 전체 백엔드 테스트 스위트 실행(회귀 확인)**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전부 PASS. `review_sync.py`/`reply_settings.py`를 건드렸으니 관련
없어 보이는 다른 파일의 테스트가 깨지지 않는지 확인하는 안전망이다.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 자동답글 실행 시점(review_sync.py)에도 Pro 게이트 적용"
```

---

### Task 3: 기존 미답변 리뷰 소급 처리

**Files:**
- Modify: `backend/app/review_sync.py` (Task 2에서 수정한 `_run_sync` 안,
  매장 루프 끝부분)
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: Task 2가 만든 `is_pro`(간접, `auto_reply_style` 조건에 이미
  반영돼 있어 직접 참조 불필요), `auto_reply_style`, `store`, `session`,
  `shop_no`, `auto_reply_errors`(모두 Task 2 이전부터 이미 `_run_sync`
  스코프에 있던 것들).
- Produces: 없음(이 태스크가 마지막 동작 변경 지점).

- [ ] **Step 1: 실패하는 테스트 작성 — 기존 미답변 리뷰가 소급으로 답글 달림**

`backend/tests/test_review_sync.py`에 추가(자동답글 테스트들 근처):

```python
def test_sync_answers_preexisting_unanswered_review_when_pro(db_session, sync_setup, reply_styles, monkeypatch):
    """자동답글을 켜기 전부터 이미 DB에 있던 미답변 5점/no_issue 리뷰도,
    Pro 매장이면 다음 동기화 때 소급으로 답글이 달려야 한다."""
    import app.review_sync as review_sync_mod
    from app.models import Review

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    backlog_review = Review(
        store_id=job.store_id, platform_id=job.platform_id, menu_summary="옛날메뉴",
        external_review_id=9001, platform_shop_no=str(_FakeSession.shop_no),
        rating=5, content="예전부터 있던 리뷰", customer_nickname="옛날고객",
        customer_order_count=1, category="no_issue", is_sensitive=False,
        sentiment_conflict=False, status="unanswered",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(backlog_review)
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])  # 이번엔 새 리뷰 없음
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: "소급 답글입니다!")
    submit_calls = []
    monkeypatch.setattr(
        review_sync_mod, "submit_reply",
        lambda page, shop_no, external_review_id, content: submit_calls.append((shop_no, external_review_id, content)),
    )

    sync_reviews_for_job(job, conn, db_session)

    db_session.refresh(backlog_review)
    assert backlog_review.status == "answered"
    assert submit_calls == [(fake_session.shop_no, 9001, "소급 답글입니다!")]
    final_reply = db_session.query(ReviewReply).filter_by(review_id=backlog_review.id, reply_type="final").one()
    assert final_reply.content == "소급 답글입니다!"


def test_sync_skips_preexisting_review_not_matching_criteria(db_session, sync_setup, reply_styles, monkeypatch):
    """기존 미답변 리뷰라도 조건(별점 5점/no_issue/비민감)을 만족 못 하면
    소급 처리 대상에서 제외된다."""
    import app.review_sync as review_sync_mod
    from app.models import Review

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    backlog_review = Review(
        store_id=job.store_id, platform_id=job.platform_id, menu_summary="옛날메뉴",
        external_review_id=9002, platform_shop_no=str(_FakeSession.shop_no),
        rating=5, content="불만 섞인 5점", customer_nickname="옛날고객2",
        customer_order_count=1, category="food_quality", is_sensitive=False,
        sentiment_conflict=False, status="unanswered",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(backlog_review)
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: pytest.fail("should not be called"))
    monkeypatch.setattr(review_sync_mod, "submit_reply", lambda *a, **kw: pytest.fail("should not be called"))

    sync_reviews_for_job(job, conn, db_session)

    db_session.refresh(backlog_review)
    assert backlog_review.status == "unanswered"


def test_sync_backlog_reply_failure_does_not_fail_whole_job(db_session, sync_setup, reply_styles, monkeypatch):
    """소급 처리 중 하나가 실패해도(예: 배민 제출 오류) job 전체가 실패로
    끝나면 안 되고, 실패 사실만 error_message에 남아야 한다(기존 신규 리뷰
    경로의 auto_reply_errors 패턴과 동일)."""
    import app.review_sync as review_sync_mod
    from app.models import Review

    job, conn = sync_setup
    _enable_auto_reply(db_session, job.store_id, reply_styles.id)

    backlog_review = Review(
        store_id=job.store_id, platform_id=job.platform_id, menu_summary="옛날메뉴",
        external_review_id=9003, platform_shop_no=str(_FakeSession.shop_no),
        rating=5, content="예전부터 있던 리뷰", customer_nickname="옛날고객3",
        customer_order_count=1, category="no_issue", is_sensitive=False,
        sentiment_conflict=False, status="unanswered",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(backlog_review)
    db_session.commit()

    fake_session = _FakeSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no, **kwargs: [])
    monkeypatch.setattr(review_sync_mod, "generate_ai_reply", lambda db, review, store, style: "답글")

    def _boom(*a, **kw):
        raise RuntimeError("배민 제출 실패")
    monkeypatch.setattr(review_sync_mod, "submit_reply", _boom)

    sync_reviews_for_job(job, conn, db_session)

    db_session.refresh(job)
    db_session.refresh(backlog_review)
    assert job.status == "success"
    assert backlog_review.status == "unanswered"
    assert "자동 답글 실패" in job.error_message
    assert "기존 미답변" in job.error_message
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "preexisting or backlog" -v`
Expected: 3개 전부 FAIL(소급 처리 코드가 아직 없어서 `backlog_review.status`가
계속 `unanswered`로 남고, 첫 번째 테스트의 `submit_calls`/`final_reply`
단언이 실패함).

- [ ] **Step 3: 소급 처리 로직 구현**

`backend/app/review_sync.py`에서 `existing_ids.add(m["external_review_id"])`
/ `total_inserted += 1`로 끝나는 신규 리뷰 처리 `for raw, m in
mapped_with_raw:` 루프 바로 다음(같은 들여쓰기 레벨 — `for shop_no,
shop_name in session.shops:` 루프 안, 다음 매장으로 넘어가기 전)에 추가:

```python
            # 자동답글을 켜기 전부터 이미 DB에 있던 미답변 리뷰는 위
            # "새로 발견된 리뷰" 루프에 절대 안 걸린다(existing_ids로
            # 걸러짐) — 그래서 한 번 자동답글을 켜도 과거 리뷰엔 영원히
            # 소급 적용이 안 되는 문제가 실사용 중 발견됐다(2026-09-26).
            # 이 매장(shop_no) 범위에서 조건을 만족하는 미답변 리뷰를 매
            # 동기화마다 다시 훑어서 같은 생성·제출 로직을 재적용한다.
            # 배민에 그 사이 사장님이 직접 답글을 달았을 가능성은 실시간
            # 재확인하지 않고 DB status만 믿는다(설계 문서 결정 사항 3번,
            # 대상이 5점/no_issue/비민감으로 한정돼 위험은 낮다고 판단).
            if auto_reply_style is not None:
                backlog = db.scalars(
                    select(Review).where(
                        Review.store_id == job.store_id,
                        Review.platform_shop_no == str(shop_no),
                        Review.status == "unanswered",
                        Review.rating >= _AUTO_REPLY_MIN_RATING_FLOOR,
                        Review.category == "no_issue",
                        Review.is_sensitive.is_(False),
                        Review.sentiment_conflict.is_(False),
                    )
                ).all()
                for review in backlog:
                    try:
                        content = generate_ai_reply(db, review, store, auto_reply_style)
                        submit_reply(session.page, shop_no, review.external_review_id, content)
                        db.add(ReviewReply(
                            review_id=review.id, reply_type="final", style_id=auto_reply_style.id,
                            content=content, created_at=datetime.now(timezone.utc),
                        ))
                        review.status = "answered"
                    except Exception as e:
                        auto_reply_errors.append(f"리뷰 {review.id}(별점 {review.rating}, 기존 미답변): {e}")
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_review_sync.py -k "preexisting or backlog" -v`
Expected: 3개 전부 PASS.

- [ ] **Step 5: 전체 백엔드 테스트 스위트 실행(회귀 확인)**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전부 PASS.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 자동답글 — 기존 미답변 리뷰도 매 동기화마다 소급 처리"
```

---

### Task 4: 프론트엔드 — `/reviews/rules` Basic 잠금

**Files:**
- Modify: `frontend/src/app/(app)/reviews/rules/page.tsx`

**Interfaces:**
- Consumes: `useStoreContext().billing.is_pro: boolean`(기존, `frontend/src/lib/store-context.tsx`에 이미 있음),
  `frontend/src/app/(app)/ads/page.tsx`의 잠금 카드 패턴(그대로 재사용, 새 컴포넌트 안 만듦).

- [ ] **Step 1: 현재 잠금 카드 패턴 확인**

`frontend/src/app/(app)/ads/page.tsx`에서 아래 블록을 그대로 참고한다(이미
존재, 새로 만들 필요 없음 — 복붙 후 문구만 바꿀 것):

```tsx
  if (billing && !billing.is_pro) {
    return (
      <div className="mx-auto max-w-md space-y-4 py-24 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent">
          🔒
        </div>
        <p className="text-lg font-semibold">Pro 전용 기능입니다</p>
        <p className="text-sm text-muted">광고 순위 모니터링은 Pro 플랜에서 이용할 수 있어요.</p>
        <Link
          href="/account/billing"
          className="inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          Pro 시작하기
        </Link>
      </div>
    );
  }
```

- [ ] **Step 2: `ReplyRulesPage`에 잠금 적용**

`frontend/src/app/(app)/reviews/rules/page.tsx` 수정:

1. 상단 import에 `useStoreContext`가 이미 있으니(7번째 줄) 그대로 두고,
   구조분해에 `billing` 추가:

```tsx
  const { storeId, billing } = useStoreContext();
```

2. `if (!settings) return ...` 바로 다음, `const currentStyle = ...` 앞에 추가:

```tsx
  if (billing && !billing.is_pro) {
    return (
      <div className="mx-auto max-w-md space-y-4 py-24 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent">
          🔒
        </div>
        <p className="text-lg font-semibold">Pro 전용 기능입니다</p>
        <p className="text-sm text-muted">자동 답글은 Pro 플랜에서 이용할 수 있어요.</p>
        <Link
          href="/account/billing"
          className="inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          Pro 시작하기
        </Link>
      </div>
    );
  }
```

(`Link`는 이미 1번째 줄에서 import돼 있음 — 새 import 불필요.)

- [ ] **Step 3: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음.

- [ ] **Step 4: 프로덕션 빌드로 라우트 구조 확인**

Run: `cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build`
Expected: 빌드 성공, `/reviews/rules` 라우트가 정적 페이지 목록에 그대로 나옴.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/app/\(app\)/reviews/rules/page.tsx
git commit -m "feat: 답글 규칙 설정 화면을 Pro 전용으로 잠금(Basic은 안내 카드)"
```

---

## 최종 검증 (모든 태스크 완료 후)

- [ ] `cd backend && .venv/bin/python -m pytest -q` — 전체 통과
- [ ] `cd frontend && npx tsc --noEmit && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build` — 통과
- [ ] `docs/superpowers/specs/2026-09-26-auto-reply-pro-gating-and-backlog-design.md`의
      "테스트 범위" 4개 항목이 전부 실제 테스트로 커버됐는지 재확인:
      (a) Basic 403/Pro 통과 → Task 1, (b) Basic이면 신규 리뷰도 자동답글 안 탐
      → Task 2, (c)+(d) 소급 처리 성공/조건불만족/부분실패 → Task 3.
