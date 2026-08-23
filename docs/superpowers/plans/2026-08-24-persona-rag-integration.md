# 페르소나 + RAG 통합 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 불만 카테고리 리뷰의 RAG 답글 생성에 페르소나별 톤 레이어를 적용하고(민감 리뷰는 강제로 진중 톤), 세일즈랩에서 유래한 4개 페르소나 이름을 우리만의 이름으로 교체하며, "직접 답글 쓰기" 모드의 초안이 리스트 재조회로 유실되는 버그를 고친다.

**Architecture:** `reply_styles`에 `tone_instruction` 컬럼을 추가해 페르소나별 톤 지시문을 DB에 둔다. `generate_ai_reply`가 `style` 파라미터를 받아 이 지시문(또는 민감 리뷰면 고정 override 문구)을 시스템 프롬프트에 얹는다. 사장님 말투 그라운딩(`store_style_profile`, 골든 예시)은 그대로 유지된다. 라우터는 `tone_overridden` 플래그를 응답에 추가하고, 프론트는 그 플래그로 안내 배지를 보여주며 직접 쓰기 초안을 `sessionStorage`로 보존한다.

**Tech Stack:** PostgreSQL, FastAPI, SQLAlchemy, Anthropic API(Claude), Next.js/React/TypeScript.

## Global Constraints

- 칭찬/무난 리뷰(`category == "no_issue"`)는 계속 무료 템플릿 치환 방식을 쓴다 — RAG로 통합하지 않는다. `template_high`/`template_mid`/`template_low` 문구 자체는 이번에 바꾸지 않는다.
- 사장님 말투 그라운딩(`store_style_profile`)과 골든 예시는 톤 레이어·민감도 override와 무관하게 항상 시스템 프롬프트에 포함된다 — 페르소나는 표면적 톤(이모지/격식)만 조절하는 얇은 레이어다.
- `is_sensitive` 또는 `sentiment_conflict`가 true면 `style.tone_instruction` 대신 고정 override 문구를 쓴다: `"위생/안전 문제이거나 별점과 내용이 어긋나는 민감한 리뷰입니다. 페르소나 톤과 무관하게 이모지 없이 차분하고 진중하게 작성하세요."`
- 4개 페르소나 새 이름/설명/톤 지시문은 정확히 아래 값을 쓴다(세일즈랩 색채 제거를 위해 여러 라운드 브레인스토밍으로 확정된 값 — 임의로 바꾸지 않는다):

| name | description | tone_instruction |
|---|---|---|
| 이모지 불맛 | 이모지를 아낌없이 써서 발랄하고 신나게 답변합니다. | 이모지를 문장마다 적극적으로 사용하고, 밝고 통통 튀는 말투로 작성하세요. |
| 담백한 손맛 | 이모지 없이 꾸밈없고 진중하게, 책임감 있는 말투로 답변합니다. | 이모지를 쓰지 않고, 격식 있고 담백한 말투로 신뢰감 있게 작성하세요. |
| 다정한 슴슴함 | 이모지를 적당히 섞어 자극적이지 않고 편안하게, 다정한 말투로 답변합니다. | 이모지를 한두 개만 은은하게 섞어, 편안하고 다정한 말투로 작성하세요. |
| 위트있는 칼칼함 | 평소엔 재치있고 유쾌하지만, 불만 리뷰에는 장난기를 빼고 진지하게 답변합니다. | 가볍고 재치있는 표현을 섞되 과하지 않게, 위트 있는 말투로 작성하세요. |

- `template_high`/`template_mid`/`template_low`는 4개 행 전부 기존 문구 그대로 유지한다(이름표만 바뀜).
- 이 프로젝트 프론트엔드에는 자동화 테스트가 없다. 프론트 검증은 `npm run build`/`npm run lint` + dev 서버 수동 확인으로 한다.
- 백엔드 테스트는 pytest, `backend/.venv/bin/python -m pytest`로 실행한다.

---

### Task 1: `reply_styles.tone_instruction` 컬럼 + 새 페르소나 이름/설명 시딩

**Files:**
- Modify: `schema.sql` (`CREATE TABLE reply_styles`, 약 99~106번째 줄)
- Modify: `backend/app/models.py` (`ReplyStyle` 클래스, 약 124~132번째 줄)
- Modify: `seed.sql` (`reply_styles` INSERT, 약 68~87번째 줄; `reply_settings` 위 주석, 90번째 줄)
- Modify: `backend/tests/conftest.py` (`reply_styles` fixture, 약 66~76번째 줄)
- Test: `backend/tests/test_llm_models.py`

**Interfaces:**
- Consumes: 없음 (스키마/시드 레벨 변경).
- Produces: `ReplyStyle.tone_instruction: str` — Task 2(`generate_ai_reply`)와 Task 3(라우터)이 `style.tone_instruction`으로 읽는다. `conftest.py`의 `reply_styles` fixture가 `tone_instruction` 필드를 갖게 되어 Task 3의 테스트가 그대로 재사용한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_llm_models.py` 파일 맨 위 임포트 줄에 `ReplyStyle`을 추가하고(기존 `from app.models import GoldenExample, OnboardingScenario, Review, StoreStyleProfile`를 `from app.models import GoldenExample, OnboardingScenario, ReplyStyle, Review, StoreStyleProfile`로), 파일 맨 아래에 아래 테스트를 추가한다:

```python
def test_reply_style_tone_instruction_round_trips(db_session):
    style = ReplyStyle(
        name="테스트 스타일", description="테스트용",
        template_high="{nickname}님 감사합니다.",
        template_mid="{nickname}님 아쉬워요.",
        template_low="{nickname}님 죄송합니다.",
        tone_instruction="이모지 없이 담백하게 작성하세요.",
    )
    db_session.add(style)
    db_session.commit()

    row = db_session.query(ReplyStyle).filter_by(id=style.id).one()
    assert row.tone_instruction == "이모지 없이 담백하게 작성하세요."
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && /Users/kunhee/Developer/review-docter/backend/.venv/bin/python -m pytest tests/test_llm_models.py -v`
Expected: FAIL — `TypeError: 'tone_instruction' is an invalid keyword argument for ReplyStyle`

- [ ] **Step 3: `models.py`에 컬럼 추가**

`backend/app/models.py`의 `ReplyStyle` 클래스를 아래로 교체:

```python
class ReplyStyle(Base):
    __tablename__ = "reply_styles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str] = mapped_column(String(200))
    template_high: Mapped[str] = mapped_column(Text)
    template_mid: Mapped[str] = mapped_column(Text)
    template_low: Mapped[str] = mapped_column(Text)
    tone_instruction: Mapped[str] = mapped_column(Text)
```

- [ ] **Step 4: `schema.sql` 갱신**

`schema.sql`의 `CREATE TABLE reply_styles` 블록(현재 아래 내용)을:

```sql
CREATE TABLE reply_styles (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(30)  NOT NULL UNIQUE,   -- 발랄 이모지 파티 / 진중맨 / 무난 요정 / 진지한 하이개그
    description   VARCHAR(200) NOT NULL,          -- 페르소나 설명 (예: 발랄한 20대 여사장님 말투)
    template_high TEXT         NOT NULL,          -- 4~5점 리뷰용 템플릿
    template_mid  TEXT         NOT NULL,          -- 3점 리뷰용 템플릿
    template_low  TEXT         NOT NULL           -- 1~2점 리뷰용 템플릿
);
```

아래로 교체:

```sql
CREATE TABLE reply_styles (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(30)  NOT NULL UNIQUE,   -- 이모지 불맛 / 담백한 손맛 / 다정한 슴슴함 / 위트있는 칼칼함
    description       VARCHAR(200) NOT NULL,          -- 페르소나 설명
    template_high     TEXT         NOT NULL,          -- 4~5점 리뷰용 템플릿(칭찬 리뷰, no_issue 전용)
    template_mid      TEXT         NOT NULL,          -- 3점 리뷰용 템플릿(칭찬 리뷰, no_issue 전용)
    template_low      TEXT         NOT NULL,          -- 1~2점 리뷰용 템플릿(칭찬 리뷰, no_issue 전용)
    tone_instruction  TEXT         NOT NULL DEFAULT '' -- 불만 리뷰 RAG 생성 시 얹는 톤 지시문(이모지/격식 수준). is_sensitive/sentiment_conflict면 고정 override로 대체됨.
);
```

- [ ] **Step 5: `seed.sql` 갱신**

`seed.sql`의 `-- 6. reply_styles` INSERT 블록(현재 4개 페르소나, 5개 값씩) 전체를 아래로 교체:

```sql
-- ----------------------------------------------------------------------------
-- 6. reply_styles — 페르소나 4종 + 별점대별 템플릿 + 불만 리뷰 RAG용 톤 지시문
--    플레이스홀더: {nickname}, {menu}, {store}
-- ----------------------------------------------------------------------------
INSERT INTO reply_styles (name, description, template_high, template_mid, template_low, tone_instruction) VALUES
('이모지 불맛', '이모지를 아낌없이 써서 발랄하고 신나게 답변합니다.',
 '{nickname}님 안녕하세요~ 💙 {menu} 맛있게 드셨다니 저희가 더 행복해요!! 🎉 {store}는 언제나 따끈따끈 준비하고 있을게요~ 또 만나요! 💛',
 '{nickname}님~ 솔직한 후기 감사해요! 🙏 {menu} 조금 아쉬우셨던 부분은 꼭 개선할게요! {store}가 다음엔 더 맛있게 준비할게요 💪',
 '{nickname}님… 속상하셨죠 😢 정말 죄송해요. {menu} 말씀 주신 부분 바로 확인하고 고치겠습니다. {store}에 한 번만 더 기회를 주세요! 🙇‍♀️',
 '이모지를 문장마다 적극적으로 사용하고, 밝고 통통 튀는 말투로 작성하세요.'),
('담백한 손맛', '이모지 없이 꾸밈없고 진중하게, 책임감 있는 말투로 답변합니다.',
 '{nickname}님, {menu}를 맛있게 드셨다니 감사합니다. 고객님의 칭찬이 가게 운영에 큰 힘이 됩니다. {store}는 언제나 최상의 음식을 제공하기 위해 노력하겠습니다.',
 '{nickname}님, 소중한 의견 감사합니다. {menu}에서 부족했던 점을 겸허히 받아들이고 개선하겠습니다. 다시 찾아주신다면 만족하실 수 있도록 하겠습니다.',
 '{nickname}님, 기대에 미치지 못해 진심으로 사과드립니다. {menu} 관련 지적해주신 사항은 즉시 개선하겠습니다. {store} 대표로서 책임지고 바로잡겠습니다.',
 '이모지를 쓰지 않고, 격식 있고 담백한 말투로 신뢰감 있게 작성하세요.'),
('다정한 슴슴함', '이모지를 적당히 섞어 자극적이지 않고 편안하게, 다정한 말투로 답변합니다.',
 '안녕하세요 {nickname}님! 😊 {menu} 맛있게 드셨다니 기분이 좋습니다! {store}를 믿고 찾아주셔서 감사드리며, 다음에도 변함없는 맛으로 보답할게요 💕',
 '{nickname}님, 후기 남겨주셔서 감사해요 😊 {menu}에서 아쉬우셨던 부분은 잘 새겨듣고 개선하겠습니다. 다음엔 꼭 만족시켜드릴게요!',
 '{nickname}님, 불편을 드려 정말 죄송합니다 🙏 {menu} 문제는 바로 확인해서 재발하지 않도록 하겠습니다. {store}가 더 나아진 모습 보여드릴게요.',
 '이모지를 한두 개만 은은하게 섞어, 편안하고 다정한 말투로 작성하세요.'),
('위트있는 칼칼함', '평소엔 재치있고 유쾌하지만, 불만 리뷰에는 장난기를 빼고 진지하게 답변합니다.',
 '{nickname}님이라는 닉네임, 명예의 전당감입니다! {menu} 만들며 돌리는 제 손길이 오늘따라 경쾌했는데 통하셨군요. {store}는 늘 이 자리에서 기다립니다!',
 '{nickname}님! 주방에 특훈 지시 내렸습니다. {menu} 다음 판은 오늘보다 한 수 위로 준비하겠습니다. 기대 반 긴장 반으로 기다려주세요!',
 '{nickname}님, 오늘은 농담을 아끼겠습니다. {menu}로 실망을 드려 죄송합니다. 말씀 주신 부분 진지하게 고치겠습니다. 다음엔 웃으며 뵙고 싶습니다.',
 '가볍고 재치있는 표현을 섞되 과하지 않게, 위트 있는 말투로 작성하세요.');
```

같은 파일의 `-- 7. reply_settings` 섹션 바로 위 주석 줄을:

```sql
-- 7. reply_settings — 가게별 답글 설정 (1호점: 발랄 이모지 파티, 2호점: 진중맨)
```

아래로 교체(주석만 갱신 — `reply_settings` INSERT 자체는 `style_id`를 정수 `1`/`2`로 참조하므로 이름이 바뀌어도 그대로 동작한다, 수정 불필요):

```sql
-- 7. reply_settings — 가게별 답글 설정 (1호점: 이모지 불맛, 2호점: 담백한 손맛)
```

- [ ] **Step 6: `conftest.py`의 `reply_styles` fixture 갱신**

`backend/tests/conftest.py`의 `reply_styles` fixture를:

```python
@pytest.fixture()
def reply_styles(db_session):
    style = ReplyStyle(
        name="발랄 이모지 파티", description="테스트용",
        template_high="{nickname}님 {menu} 최고예요! {store} 감사합니다.",
        template_mid="{nickname}님 {menu} 아쉬웠어요. {store} 개선할게요.",
        template_low="{nickname}님 {menu} 죄송합니다. {store} 바로 고칠게요.",
    )
    db_session.add(style)
    db_session.flush()
    return style
```

아래로 교체:

```python
@pytest.fixture()
def reply_styles(db_session):
    style = ReplyStyle(
        name="이모지 불맛", description="테스트용",
        template_high="{nickname}님 {menu} 최고예요! {store} 감사합니다.",
        template_mid="{nickname}님 {menu} 아쉬웠어요. {store} 개선할게요.",
        template_low="{nickname}님 {menu} 죄송합니다. {store} 바로 고칠게요.",
        tone_instruction="이모지를 문장마다 적극적으로 사용하고, 밝고 통통 튀는 말투로 작성하세요.",
    )
    db_session.add(style)
    db_session.flush()
    return style
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `cd backend && /Users/kunhee/Developer/review-docter/backend/.venv/bin/python -m pytest tests/test_llm_models.py -v`
Expected: PASS (전체)

- [ ] **Step 8: 전체 스위트 회귀 확인**

Run: `cd backend && /Users/kunhee/Developer/review-docter/backend/.venv/bin/python -m pytest -q`
Expected: 이전에 통과하던 테스트가 전부 그대로 통과한다(이 시점에는 아직 `generate_ai_reply`/라우터가 `style`을 요구하지 않으므로 실패가 없어야 한다). 만약 `test_reviews.py`나 `test_llm_generate.py`에서 `reply_styles` fixture를 쓰는 테스트가 실패한다면, 그 테스트가 `ReplyStyle(...)`을 직접 생성하면서 `tone_instruction`을 빠뜨린 것이니 Task 2/3에서 마저 고친다(지금 이 태스크에서는 fixture 기반 테스트만 통과하면 충분).

- [ ] **Step 9: 커밋**

```bash
git add schema.sql backend/app/models.py seed.sql backend/tests/conftest.py backend/tests/test_llm_models.py
git commit -m "feat: reply_styles에 tone_instruction 컬럼 추가 + 세일즈랩 색채 없는 새 페르소나 이름/설명 시딩"
```

---

### Task 2: `generate_ai_reply`에 페르소나 톤 레이어 + 민감 리뷰 강제 override

**Files:**
- Modify: `backend/app/llm/generate.py`
- Test: `backend/tests/test_llm_generate.py`

**Interfaces:**
- Consumes: `ReplyStyle.tone_instruction: str`(Task 1).
- Produces: `generate_ai_reply(db: Session, review: Review, store: Store, style: ReplyStyle) -> str` — 시그니처가 `style` 파라미터를 새로 받는다. Task 3(라우터)이 이 새 시그니처로 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_llm_generate.py`의 기존 3개 테스트 전부 `generate.generate_ai_reply(db_session, review, seeded_user["store"])` 호출에 4번째 인자가 빠져 있다. 파일 전체를 아래로 교체한다(기존 3개 테스트에 `reply_styles` fixture와 4번째 인자를 추가하고, 새 테스트 4개를 더한다):

```python
from datetime import datetime, timezone

from app.llm import generate
from app.models import GoldenExample, Review, StoreStyleProfile


def test_generate_ai_reply_includes_style_profile_and_examples(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, rules="- 구체적 원인을 설명한다", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    db_session.add(GoldenExample(
        store_id=sid, category="hygiene", review_text="옛날 리뷰", reply_text="옛날 답글",
        is_manual=True, is_synthetic=False, source="backfill", created_at=datetime.now(timezone.utc),
    ))
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=5, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=3, category="hygiene", is_sensitive=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return "죄송합니다, 확인하겠습니다."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    result = generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert result == "죄송합니다, 확인하겠습니다."
    assert "구체적 원인을 설명한다" in captured["system"]
    assert "옛날 리뷰" in captured["system"]
    assert "내용을 그대로 복사하지" in captured["system"]  # 안전장치 지시가 포함됐는지
    assert "재방문" in captured["user"] or "3회" in captured["user"]  # 재방문 고객 정보 반영
    assert "이물질이 나왔어요" in captured["user"]


def test_generate_ai_reply_without_style_profile_uses_fallback_instruction(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=2, content="배달이 늦었어요",
        customer_nickname="손님", customer_order_count=1, category="delivery",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    def _fake_call_sonnet(system, user, **kw):
        return "죄송합니다."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    result = generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert result == "죄송합니다."


def test_generate_ai_reply_injects_sensitive_instruction(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene", is_sensitive=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["user"] = user
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "민감" in captured["user"] or "신중" in captured["user"]


def test_generate_ai_reply_includes_persona_tone_instruction_when_not_sensitive(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=2, content="배달이 늦었어요",
        customer_nickname="손님", customer_order_count=1, category="delivery",
        is_sensitive=False, sentiment_conflict=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert reply_styles.tone_instruction in captured["system"]


def test_generate_ai_reply_overrides_tone_when_sensitive(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene",
        is_sensitive=True, sentiment_conflict=False, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert generate._SENSITIVE_TONE_OVERRIDE in captured["system"]
    assert reply_styles.tone_instruction not in captured["system"]  # 페르소나 톤이 완전히 대체됐는지


def test_generate_ai_reply_overrides_tone_when_sentiment_conflict(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=5, content="배달이 너무 늦었어요",
        customer_nickname="손님", customer_order_count=1, category="delivery",
        is_sensitive=False, sentiment_conflict=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert generate._SENSITIVE_TONE_OVERRIDE in captured["system"]
    assert reply_styles.tone_instruction not in captured["system"]


def test_generate_ai_reply_grounding_present_even_when_tone_overridden(db_session, seeded_user, platforms, reply_styles, monkeypatch):
    """톤이 override돼도 사장님 말투 그라운딩(store_style_profile)과 골든
    예시는 그대로 시스템 프롬프트에 남아있어야 한다 — 톤 레이어는 표면적
    조절일 뿐 그라운딩을 대체하지 않는다."""
    sid = seeded_user["store"].id
    pid = platforms["baemin"].id
    db_session.add(StoreStyleProfile(
        store_id=sid, rules="- 항상 재방문을 유도한다", generated_from_count=1,
        updated_at=datetime.now(timezone.utc),
    ))
    review = Review(
        store_id=sid, platform_id=pid, menu_summary="치킨", rating=1, content="이물질이 나왔어요",
        customer_nickname="손님", customer_order_count=1, category="hygiene",
        is_sensitive=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    captured = {}

    def _fake_call_sonnet(system, user, **kw):
        captured["system"] = system
        return "..."

    monkeypatch.setattr(generate.client, "call_sonnet", _fake_call_sonnet)

    generate.generate_ai_reply(db_session, review, seeded_user["store"], reply_styles)

    assert "항상 재방문을 유도한다" in captured["system"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && /Users/kunhee/Developer/review-docter/backend/.venv/bin/python -m pytest tests/test_llm_generate.py -v`
Expected: FAIL — `TypeError: generate_ai_reply() takes 3 positional arguments but 4 were given` (기존 3개), 새 테스트들은 `AttributeError: module 'app.llm.generate' has no attribute '_SENSITIVE_TONE_OVERRIDE'` 등으로 실패

- [ ] **Step 3: `generate.py` 구현**

`backend/app/llm/generate.py` 전체를 아래로 교체:

```python
"""문제 리뷰(category != "no_issue")에 대한 RAG 기반 답글 생성. 검색
(app.llm.rag)과 생성(Sonnet)을 조합한다 — 벡터 검색은 쓰지 않는다.

페르소나(reply_styles.tone_instruction)는 표면적 톤(이모지 사용량, 격식
수준)만 조절하는 얇은 레이어다 — 원인 설명·사과의 실질적 근거는 항상
store_style_profile(사장님 말투 그라운딩)과 골든 예시에서만 온다. 위생/안전
민감 사안이거나 별점-내용이 어긋나는 리뷰는 페르소나 선택과 무관하게
_SENSITIVE_TONE_OVERRIDE로 강제 전환한다(설계 문서
2026-08-24-persona-rag-integration-design.md 참고)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client
from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import ReplyStyle, Review, Store, StoreStyleProfile

_FALLBACK_STYLE_RULES = "아직 학습된 스타일이 없습니다. 정중하고 진솔한 사과문 원칙을 따르세요."

_SENSITIVE_TONE_OVERRIDE = (
    "위생/안전 문제이거나 별점과 내용이 어긋나는 민감한 리뷰입니다. "
    "페르소나 톤과 무관하게 이모지 없이 차분하고 진중하게 작성하세요."
)

CATEGORY_LABELS = {
    "food_quality": "음식 품질(맛/온도/양)",
    "delivery": "배달(지연/파손)",
    "hygiene": "위생/이물질",
    "service": "응대",
    "price": "가격",
    "missing_or_wrong_item": "오배송/누락",
}


def _build_system_prompt(store: Store, style_rules: str, examples, tone_instruction: str) -> str:
    example_block = "\n\n".join(
        f'예시 {i}: 리뷰 "{ex.review_text}" / 답글 "{ex.reply_text}"'
        for i, ex in enumerate(examples, start=1)
    ) if examples else "(아직 참고할 예시가 없습니다.)"

    return f"""너는 "{store.name}"의 사장님을 대신해 배달앱 리뷰에 답글을 쓴다.

[이 가게의 답글 스타일]
{style_rules}

[답글 톤]
{tone_instruction}

[참고 예시 — 스타일 참고 전용]
아래는 이 가게 사장님이 실제로 쓴(또는 승인한) 답글 예시다.
**절대 지켜야 할 규칙**: 이 예시들은 말투·태도·구조(원인 설명 → 사과 →
재방문 유도)만 참고하라. 문장 내용을 그대로 복사하지 말고, 구체적 원인은
반드시 "이번 리뷰의 실제 상황"에만 근거해 새로 작성하라.

{example_block}

위 지시를 지켜 답글만 출력하고 다른 설명은 붙이지 마라."""


def _build_user_message(review: Review, category_label: str, repeat_count: int) -> str:
    lines = [
        f"별점: {review.rating}",
        f"불만 유형: {category_label}",
        f'내용: "{review.content}"',
        f"이 고객의 누적 주문 횟수: {review.customer_order_count}회",
    ]
    if review.customer_order_count > 1:
        lines.append("재방문 고객이니 자연스럽게 반영하세요.")
    if repeat_count > 1:
        lines.append(f"이 유형 불만이 최근 30일간 {repeat_count}건째입니다 — 반복 문제임을 인지하되 변명처럼 들리지 않게 주의하세요.")
    if review.is_sensitive:
        lines.append("위생/안전 관련 민감 사안입니다. 섣부른 원인 추정이나 과도한 변명 없이, 진지하게 사과하고 구체적 조치(연락처 안내 등)를 제시하세요.")
    return "\n".join(lines)


def generate_ai_reply(db: Session, review: Review, store: Store, style: ReplyStyle) -> str:
    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store.id))
    style_rules = profile.rules if profile is not None else _FALLBACK_STYLE_RULES

    examples = fetch_golden_examples(db, store.id, review.category, limit=3)
    repeat_count = count_recent_same_category(db, store.id, review.category, days=30)
    category_label = CATEGORY_LABELS.get(review.category, review.category)

    tone_instruction = (
        _SENSITIVE_TONE_OVERRIDE
        if review.is_sensitive or review.sentiment_conflict
        else style.tone_instruction
    )

    system_prompt = _build_system_prompt(store, style_rules, examples, tone_instruction)
    user_message = _build_user_message(review, category_label, repeat_count)
    return client.call_sonnet(system_prompt, user_message, max_tokens=800)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && /Users/kunhee/Developer/review-docter/backend/.venv/bin/python -m pytest tests/test_llm_generate.py -v`
Expected: PASS (전체 7개)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/llm/generate.py backend/tests/test_llm_generate.py
git commit -m "feat: generate_ai_reply에 페르소나 톤 레이어 + 민감 리뷰 강제 진중 override 추가"
```

---

### Task 3: 라우터 — `style` 전달 + `tone_overridden` 응답 플래그

**Files:**
- Modify: `backend/app/routers/reviews.py:113-155`(`generate_reply` 함수)
- Test: `backend/tests/test_reviews.py`

**Interfaces:**
- Consumes: `generate_ai_reply(db, review, store, style)`(Task 2의 새 시그니처).
- Produces: `POST /reviews/{id}/generate-reply` 응답이 `{"content": str, "style_id": int, "tone_overridden": bool}`을 반환한다 — Task 4(프론트엔드)가 `tone_overridden`을 읽어 안내 배지를 띄운다.

**주의**: `test_reviews.py`에 이미 `reviews_mod.generate_ai_reply`를 `monkeypatch`하는 테스트가 2개 있고(`test_generate_reply_uses_ai_path_for_problem_review`, `test_generate_reply_returns_503_with_korean_error_when_ai_generation_fails`), 둘 다 옛 3-인자 시그니처(`lambda db, review, store: ...`)로 patch돼 있다. 라우터가 4번째 인자(`style`)를 넘기도록 바뀌면 이 두 lambda가 `TypeError`를 던지므로, 이번 태스크에서 반드시 같이 고쳐야 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_reviews.py`에서 아래 두 곳을 수정하고, 새 테스트 3개를 추가한다.

기존(210번째 줄 근처) `test_generate_reply_uses_ai_path_for_problem_review`의 이 줄:

```python
    monkeypatch.setattr(reviews_mod, "generate_ai_reply", lambda db, review, store: "AI가 만든 답글입니다.")
```

을:

```python
    monkeypatch.setattr(reviews_mod, "generate_ai_reply", lambda db, review, store, style: "AI가 만든 답글입니다.")
```

로 교체.

기존(234번째 줄 근처) `test_generate_reply_returns_503_with_korean_error_when_ai_generation_fails`의 이 부분:

```python
    def _raise(db, review, store):
        raise RuntimeError("네트워크 오류")
```

을:

```python
    def _raise(db, review, store, style):
        raise RuntimeError("네트워크 오류")
```

로 교체.

파일 끝에 아래 3개 테스트를 추가한다:

```python
def test_generate_reply_tone_overridden_true_when_sensitive(client, db_session, seeded_user, platforms, auth_headers, reply_styles, monkeypatch):
    from datetime import datetime, timezone

    from app.models import Review
    from app.routers import reviews as reviews_mod

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=1, content="이물질이 나왔어요", customer_nickname="손님",
        category="hygiene", is_sensitive=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    monkeypatch.setattr(reviews_mod, "generate_ai_reply", lambda db, review, store, style: "답글")

    res = client.post(
        f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers,
    )

    assert res.status_code == 200
    assert res.json()["tone_overridden"] is True


def test_generate_reply_tone_overridden_false_when_not_sensitive(client, db_session, seeded_user, platforms, auth_headers, reply_styles, monkeypatch):
    from datetime import datetime, timezone

    from app.models import Review
    from app.routers import reviews as reviews_mod

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=2, content="배달이 늦었어요", customer_nickname="손님",
        category="delivery", is_sensitive=False, sentiment_conflict=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    monkeypatch.setattr(reviews_mod, "generate_ai_reply", lambda db, review, store, style: "답글")

    res = client.post(
        f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers,
    )

    assert res.status_code == 200
    assert res.json()["tone_overridden"] is False


def test_generate_reply_tone_overridden_false_for_no_issue_review(client, db_session, seeded_user, platforms, auth_headers, reply_styles):
    from datetime import datetime, timezone

    from app.models import Review

    review = Review(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        menu_summary="치킨", rating=5, content="맛있어요", customer_nickname="손님",
        category="no_issue", created_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()

    res = client.post(
        f"/reviews/{review.id}/generate-reply", json={"style_id": reply_styles.id}, headers=auth_headers,
    )

    assert res.status_code == 200
    assert res.json()["tone_overridden"] is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && /Users/kunhee/Developer/review-docter/backend/.venv/bin/python -m pytest tests/test_reviews.py -v`
Expected: FAIL — 새 3개 테스트는 `KeyError: 'tone_overridden'`, 기존 2개(이미 고친) 테스트는 라우터가 아직 3-인자로 호출하므로 오히려 지금은 통과할 수 있음(다음 스텝에서 라우터를 바꾸면 일관됨) — 어느 쪽이든 최종적으로 Step 4에서 전체가 PASS하면 된다.

- [ ] **Step 3: 라우터 구현**

`backend/app/routers/reviews.py`의 `generate_reply` 함수(113~155번째 줄) 중 아래 부분을:

```python
    if review.category == "no_issue":
        template = {"low": style.template_low, "mid": style.template_mid, "high": style.template_high}[_band(review.rating)]
        content = _fill_template(template, review, review.store)
    else:
        try:
            content = generate_ai_reply(db, review, review.store)
        except Exception:
            raise HTTPException(
                503,
                detail={"message": "AI 답글 생성에 실패했어요. 잠시 후 다시 시도해주세요.", "error_code": "ai_generation_failed"},
            )

    draft = ReviewReply(
        review_id=review.id, reply_type="ai_draft", style_id=style.id,
        content=content, created_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    if review.status == "unanswered":
        review.status = "pending"
    db.commit()
    return {"content": content, "style_id": style.id}
```

아래로 교체:

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

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && /Users/kunhee/Developer/review-docter/backend/.venv/bin/python -m pytest tests/test_reviews.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 전체 스위트 회귀 확인**

Run: `cd backend && /Users/kunhee/Developer/review-docter/backend/.venv/bin/python -m pytest -q`
Expected: 전부 PASS, 새로운 실패 없음

- [ ] **Step 6: 커밋**

```bash
git add backend/app/routers/reviews.py backend/tests/test_reviews.py
git commit -m "feat: generate-reply 라우터가 style을 RAG에 전달하고 tone_overridden 플래그를 응답"
```

---

### Task 4: 프론트엔드 — 민감 리뷰 안내 배지 + 직접 쓰기 초안 sessionStorage 보존

**Files:**
- Modify: `frontend/src/app/(app)/reviews/page.tsx`

**Interfaces:**
- Consumes: `POST /reviews/{id}/generate-reply` 응답의 `tone_overridden: boolean`(Task 3).
- Produces: 없음 (이 플랜의 마지막 태스크).

이 태스크는 자동화 테스트가 없다 — "구현 → 빌드/린트 확인 → 수동 체크리스트" 순서로 진행한다.

- [ ] **Step 1: `ReviewCard` 컴포넌트 전체를 아래 코드로 교체**

`frontend/src/app/(app)/reviews/page.tsx`에서 `function ReviewCard({...`부터 그 함수의 닫는 `}`까지(현재 114번째 줄부터 376번째 줄까지) 전체를, 그리고 그 바로 앞에 sessionStorage 헬퍼 3개 함수를 추가한다.

`function ReviewCard({` 바로 앞에 추가:

```tsx
type SavedDraft = { mode: "manual" | "ai"; draft: string };

function loadSavedDraft(reviewId: number): SavedDraft | null {
  try {
    const raw = sessionStorage.getItem(`review-draft-${reviewId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && (parsed.mode === "manual" || parsed.mode === "ai") && typeof parsed.draft === "string") {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

function saveDraftToStorage(reviewId: number, mode: "idle" | "manual" | "ai", draft: string) {
  try {
    if (mode === "idle") {
      sessionStorage.removeItem(`review-draft-${reviewId}`);
    } else {
      sessionStorage.setItem(`review-draft-${reviewId}`, JSON.stringify({ mode, draft }));
    }
  } catch {
    // sessionStorage 사용 불가(프라이빗 브라우징 등) — 초안 보존은 best-effort이므로 조용히 무시
  }
}

function clearSavedDraft(reviewId: number) {
  try {
    sessionStorage.removeItem(`review-draft-${reviewId}`);
  } catch {
    // ignore
  }
}
```

그다음 `ReviewCard` 함수 전체를 아래로 교체:

```tsx
function ReviewCard({
  review, styles, onSaved, brandName,
}: {
  review: Review; styles: ReplyStyle[]; onSaved: () => void; brandName?: string;
}) {
  const { refreshBilling } = useStoreContext();
  const savedDraft = loadSavedDraft(review.id);
  const [mode, setMode] = useState<"idle" | "manual" | "ai">(
    savedDraft?.mode ?? (review.draft_reply ? "ai" : "idle")
  );
  const [styleId, setStyleId] = useState(review.draft_reply?.style_id ?? styles[0]?.id ?? 0);
  const [draft, setDraft] = useState(savedDraft?.draft ?? review.draft_reply?.content ?? "");
  const [lastGenerated, setLastGenerated] = useState(review.draft_reply?.content ?? "");
  const [toneOverridden, setToneOverridden] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [secondaryText, setSecondaryText] = useState("");
  const [savingSecondary, setSavingSecondary] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  useEffect(() => {
    if (styles.length > 0 && styleId === 0) setStyleId(styles[0].id);
  }, [styles, styleId]);

  useEffect(() => {
    saveDraftToStorage(review.id, mode, draft);
  }, [review.id, mode, draft]);

  const generate = async () => {
    setGenerating(true);
    setGenerateError(null);
    try {
      const res = await apiPost<{ content: string; tone_overridden: boolean }>(
        `/reviews/${review.id}/generate-reply`, { style_id: styleId }
      );
      setDraft(res.content);
      setLastGenerated(res.content);
      setToneOverridden(res.tone_overridden);
      setMode("ai");
      // onSaved()를 여기서 부르지 않는다 — 목록을 새로고침하면 이 리뷰가
      // 서버에서 이미 pending으로 바뀌어 "답글 대기" 필터에서 사라지고,
      // 사장님이 미리보기를 확인·수정하기도 전에 카드가 없어져 버린다.
      // 목록 갱신은 최종 "답글 등록"을 눌렀을 때만 한다.
      await refreshBilling();
    } catch (e) {
      if (e instanceof ApiError && e.errorCode === "reply_limit_exceeded") {
        setGenerateError(e.message);
      } else {
        setGenerateError(e instanceof ApiError ? e.message : "답글 생성에 실패했습니다.");
      }
    } finally {
      setGenerating(false);
    }
  };

  const regenerate = async () => {
    // 마지막 생성 결과와 지금 텍스트가 다르면(직접 손을 댄 흔적이 있으면)
    // 되돌릴 방법이 없으니 실수로 날리지 않도록 한 번 확인한다.
    if (draft !== lastGenerated) {
      const confirmed = window.confirm("지금까지 수정한 내용이 사라집니다. 다시 생성할까요?");
      if (!confirmed) return;
    }
    await generate();
  };

  const startManual = () => {
    setGenerateError(null);
    setMode("manual");
    setDraft("");
  };

  const cancelDraft = () => {
    setMode("idle");
    setDraft("");
    setGenerateError(null);
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPost(`/reviews/${review.id}/reply`, { style_id: mode === "ai" ? styleId : null, content: draft });
      clearSavedDraft(review.id);
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const saveSecondary = async () => {
    if (!secondaryText.trim()) return;
    setSavingSecondary(true);
    try {
      await apiPost(`/reviews/${review.id}/secondary-reply`, { content: secondaryText });
      setSecondaryText("");
      onSaved();
    } finally {
      setSavingSecondary(false);
    }
  };

  return (
    <div className="rounded-xl border border-border-subtle bg-surface-2 p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="rounded bg-surface px-2 py-0.5 font-medium text-accent">{review.platform_name}</span>
        {brandName && (
          <span className="rounded bg-surface px-2 py-0.5 font-medium text-foreground">{brandName}</span>
        )}
        {review.category !== "no_issue" && (
          <span
            className={`rounded bg-surface px-2 py-0.5 font-medium ${
              review.is_sensitive ? "text-danger" : "text-warning"
            }`}
          >
            {review.is_sensitive ? "⚠ " : ""}
            {CATEGORY_LABELS[review.category] ?? review.category}
          </span>
        )}
        <span className="text-warning">{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span>
        <span className="font-medium text-foreground">{review.customer_nickname}</span>
        <span>· {review.customer_order_count}회 주문</span>
        <span className="ml-auto">{new Date(review.created_at).toLocaleString("ko-KR")}</span>
      </div>
      <p className="mt-2 text-xs text-muted">{review.menu_summary}</p>
      <p className="mt-1 text-sm text-foreground">{review.content}</p>

      {review.final_reply ? (
        <div className="mt-3 space-y-2">
          <div className="rounded-lg border border-border-subtle bg-surface p-3">
            <p className="mb-1 text-xs font-medium text-success">등록된 답글</p>
            <p className="text-sm text-foreground">{review.final_reply.content}</p>
          </div>
          {review.secondary_replies.map((r) => (
            <div key={r.id} className="rounded-lg border border-accent/30 bg-accent-soft p-3">
              <p className="mb-1 text-xs font-medium text-accent">2차 답글</p>
              <p className="text-sm text-foreground">{r.content}</p>
            </div>
          ))}
          <div className="flex gap-2">
            <input
              value={secondaryText}
              onChange={(e) => setSecondaryText(e.target.value)}
              placeholder="추가로 안내할 내용을 입력하세요"
              className="flex-1 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
            <button
              onClick={saveSecondary}
              disabled={savingSecondary || !secondaryText.trim()}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {savingSecondary ? "등록 중..." : "2차 답글 등록"}
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          {mode === "idle" && !generateError && (
            <div className="flex flex-wrap gap-2">
              <button
                onClick={startManual}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs font-medium text-muted transition hover:text-foreground"
              >
                직접 답글 쓰기
              </button>
              <button
                onClick={generate}
                disabled={generating || styles.length === 0}
                className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {generating ? "생성 중..." : "✨ AI 추천 답글 보기"}
              </button>
            </div>
          )}

          {generateError && (
            <div className="space-y-2">
              <p className="text-xs text-danger">
                {generateError}{" "}
                <Link href="/account/billing" className="underline">
                  구독 관리
                </Link>
              </p>
              {mode === "idle" && (
                <button
                  onClick={startManual}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs font-medium text-muted transition hover:text-foreground"
                >
                  대신 직접 답글 쓰기
                </button>
              )}
            </div>
          )}

          {mode === "ai" && (
            <div className="space-y-2 rounded-lg border border-accent/40 bg-accent-soft/40 p-3">
              {toneOverridden && (
                <p className="rounded-lg bg-warning/10 px-2 py-1 text-xs text-warning">
                  ⚠ 민감한 리뷰라 자동으로 진중한 톤으로 작성돼요
                </p>
              )}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-medium text-accent">AI 추천 답글</p>
                <div className="flex items-center gap-2">
                  <select
                    value={styleId}
                    onChange={(e) => setStyleId(Number(e.target.value))}
                    className="rounded-lg border border-border-subtle bg-surface px-2 py-1 text-xs outline-none focus:border-accent"
                  >
                    {styles.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                  <button
                    onClick={regenerate}
                    disabled={generating}
                    title="다시 생성"
                    className="rounded-lg border border-border-subtle p-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                  >
                    {generating ? "..." : "↻"}
                  </button>
                </div>
              </div>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-border-subtle bg-surface p-2.5 text-sm outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={save}
                  disabled={saving || !draft.trim()}
                  className="rounded-lg bg-success px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "등록 중..." : "이대로 답글 등록"}
                </button>
                <button
                  onClick={cancelDraft}
                  disabled={saving}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                >
                  취소
                </button>
              </div>
            </div>
          )}

          {mode === "manual" && (
            <div className="space-y-2 rounded-lg border border-border-subtle bg-surface p-3">
              <p className="text-xs font-medium text-muted">직접 작성</p>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                autoFocus
                placeholder="답글을 입력하세요"
                className="w-full rounded-lg border border-border-subtle bg-surface-2 p-2.5 text-sm outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={save}
                  disabled={saving || !draft.trim()}
                  className="rounded-lg bg-success px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "등록 중..." : "이대로 답글 등록"}
                </button>
                <button
                  onClick={cancelDraft}
                  disabled={saving}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                >
                  취소
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

이 교체로 바뀌는 부분: 모듈 최상단에 `loadSavedDraft`/`saveDraftToStorage`/`clearSavedDraft` 3개 헬퍼 함수 추가, `mode`/`draft`의 초기값이 `sessionStorage`를 먼저 확인하도록 변경, 새 상태 `toneOverridden` 추가, `mode`/`draft` 변경 시마다 `sessionStorage`에 저장하는 `useEffect` 추가, `generate()`가 `tone_overridden`을 읽어 상태에 반영, `save()` 성공 시 `clearSavedDraft` 호출, AI 패널에 민감 리뷰 안내 배지 추가. 헤더, `review.final_reply` 블록, `saveSecondary`, `startManual`, `cancelDraft`, "직접 쓰기" 패널의 나머지는 기존 그대로다.

- [ ] **Step 2: 빌드로 타입 오류 확인**

Run: `cd frontend && npm run build`
Expected: 에러 없이 빌드 성공.

- [ ] **Step 3: 린트 확인**

Run: `cd frontend && npm run lint`
Expected: 이 파일에서 새로 생긴 에러/경고가 없어야 한다(기존에 이미 있던 다른 파일들의 `react-hooks/set-state-in-effect` 계열 경고는 무시).

- [ ] **Step 4: dev 서버에서 직접 확인**

Run: `cd frontend && npm run dev` (백엔드도 `ANTHROPIC_API_KEY`가 설정된 상태로 함께 떠 있어야 실제 AI 호출까지 확인 가능)

- "답글 스타일 설정" 페이지에서 4개 페르소나 이름이 "이모지 불맛/담백한 손맛/다정한 슴슴함/위트있는 칼칼함"으로 보이는지
- 위생 카테고리(민감) 리뷰에서 "AI 추천 답글 보기" → "⚠ 민감한 리뷰라 자동으로 진중한 톤으로 작성돼요" 배지가 뜨고, 실제 답글에 이모지가 없는지
- 민감하지 않은 불만 리뷰(예: 가격 불만)에서 페르소나를 바꿔가며 "다시 생성" → 이모지 사용량/격식 수준이 실제로 다르게 나오는지(예: "이모지 불맛" vs "담백한 손맛")
- 별점 5점인데 불만 내용이 섞인 리뷰(sentiment_conflict, 실제로 만들려면 리뷰 동기화 시점 분류에 의존하므로 DB에서 직접 `sentiment_conflict=true`로 만든 테스트 리뷰 사용)에서도 배지가 뜨는지
- "직접 답글 쓰기"로 타이핑 → 필터를 "답글 대기"→"전체"→"답글 대기"로 바꿔 카드가 사라졌다 다시 나타나게 함 → 타이핑한 내용이 복구되는지
- 위 상태에서 페이지를 새로고침해도 내용이 복구되는지
- "이대로 답글 등록" 성공 후에는 그 리뷰의 sessionStorage 항목이 지워져서, 이후 우연히 같은 키로 다른 상태가 복구되지 않는지(개발자도구 Application 탭에서 `review-draft-{id}` 키가 저장 후 사라지는지 확인)

- [ ] **Step 5: 커밋**

```bash
git add "frontend/src/app/(app)/reviews/page.tsx"
git commit -m "feat: 민감 리뷰 톤 override 안내 배지 + 직접 쓰기 초안 sessionStorage 보존"
```

---

## Self-Review 메모 (플랜 작성자용, 실행 시 참고만)

- **스펙 커버리지**: 설계 문서의 `tone_instruction` 컬럼 추가(Task 1), 4개 페르소나 새 이름/설명/톤 지시문(Task 1), RAG 톤 레이어 + 민감 override(Task 2), 라우터의 `style` 전달 + `tone_overridden` 응답(Task 3), 프론트 안내 배지 + sessionStorage 초안 보존(Task 4) 전부 매핑됨. 비목표(no_issue 템플릿 유지, template_high/mid/low 문구 불변, 이미지 첨부 제외)는 Global Constraints와 각 태스크 코드에서 명시적으로 지켜짐.
- **플레이스홀더 스캔**: "TODO" 등 없음. 모든 코드 블록이 실행 가능한 완성 코드.
- **타입/시그니처 일관성**: `generate_ai_reply(db, review, store, style)` 시그니처가 Task 2에서 정의되고 Task 3의 라우터 코드·테스트 monkeypatch 전부 동일한 4-인자 형태로 일관됨. `tone_overridden` 필드명이 Task 3의 라우터 응답과 Task 4의 프론트 타입/JSX에서 동일하게 쓰임. `reply_styles.tone_instruction`이 Task 1(모델/스키마/시드/fixture)부터 Task 2(읽는 쪽)까지 이름이 일관됨.
- **회귀 위험 지점**: Task 3에서 명시했듯, 라우터 시그니처 변경이 `test_reviews.py`의 기존 2개 monkeypatch 테스트를 깨뜨리므로 그 수정을 Task 3의 Step 1에 포함시켰다 — 별도 태스크로 빠뜨리지 않았는지 재확인 완료.
