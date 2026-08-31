"""모든 리뷰(칭찬/무난 포함)에 대한 RAG 기반 답글 생성. 검색(app.llm.rag)과
생성(Sonnet)을 조합한다 — 벡터 검색은 쓰지 않는다.

원래는 category == "no_issue"(칭찬/무난) 리뷰만 무료 템플릿 치환으로
처리하고 이 RAG 경로는 문제 리뷰에만 탔다. 그런데 실사용 중 "기본맛으로
주문했는데 맵지 않아요, 조금 더 매우면 맛있을 것 같아요"처럼 별점은
높지만 구체적인 피드백이 담긴 리뷰에도 템플릿이 리뷰 내용과 무관한
정형 문구("맛있게 드셨다니 다행입니다")를 그대로 붙이는 문제가
확인됐다(2026-08-24) — 카테고리 분류가 "명백한 불만"이 아니면 전부
no_issue로 묶이기 때문에, 템플릿 경로는 이런 리뷰의 구체적 내용을 전혀
반영하지 못한다. 그래서 no_issue를 포함한 모든 리뷰가 이 RAG 경로를
타도록 바꿨다 — 사장님이 실제로 답글을 원하는 이상 항상 사장님의 학습된
말투로 응답하는 게 "찐사장님 말투"라는 이 기능의 원래 목적에 맞다는
판단(사용자 확인). reply_styles.template_high/mid/low 컬럼은 이제 이
경로에서 더 이상 읽지 않는다 — 컬럼 자체는 되돌릴 여지를 남기려고 스키마에
그대로 뒀다(DROP 안 함).

페르소나(reply_styles.tone_instruction)는 표면적 톤(이모지 사용량, 격식
수준)만 조절하는 얇은 레이어다 — 원인 설명·사과의 실질적 근거는 항상
store_style_profile(사장님 말투 그라운딩)과 골든 예시에서만 온다. no_issue가
아닌 모든 불만 리뷰(위생/안전 민감 사안, 별점-내용 불일치 포함)는 페르소나
선택과 무관하게 _COMPLAINT_TONE_OVERRIDE로 강제 전환한다(설계 문서
2026-08-24-persona-rag-integration-design.md 참고, 2026-08-31 실사용 중
food_quality 등 일반 불만 리뷰에도 이모지가 섞여 나오는 문제가 확인돼
is_sensitive/sentiment_conflict 두 조건에서 "no_issue가 아닌 모든 불만"으로
범위를 넓혔다)."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client
from app.llm.rag import count_recent_same_category, fetch_golden_examples
from app.models import BaeminShopBrand, BrandMenuInfo, ReplyStyle, Review, Store, StorePlatformConnection, StoreStyleProfile

_FALLBACK_STYLE_RULES = "아직 학습된 스타일이 없습니다. 정중하고 진솔한 사과문 원칙을 따르세요."

_COMPLAINT_TONE_OVERRIDE = (
    "불만이 담긴 리뷰입니다(위생/안전 문제이거나 별점과 내용이 어긋나는 경우 포함). "
    "페르소나 톤과 무관하게 이모지 없이 차분하고 진중하게 작성하세요."
)

# 이모지 유니코드 대역 — 실사용 중 불만 리뷰(is_sensitive/sentiment_conflict)에
# "이모지 없이"라고 지시해도 이모지가 섞여 나오는 문제가 확인됐다(2026-08-26).
# 원인은 [참고 예시]에 넣는 골든 예시 자체가 사장님의 실제 과거 답글이라 이모지가
# 섞여있는 경우가 많아, 프롬프트 지시문과 few-shot 예시가 서로 모순되는
# 신호를 주기 때문이다 — 텍스트 지시만으로는 안정적으로 이길 수 없다. 그래서
# 불만 리뷰일 때는 (1) 프롬프트에 넣는 예시 텍스트에서부터 이모지를 지워
# 모순되는 신호 자체를 없애고, (2) 최종 생성 결과에서도 한 번 더 걸러내
# 확정적으로 보장한다. 처음엔 is_sensitive/sentiment_conflict 두 조건에만
# 적용했는데, food_quality처럼 그 둘에 안 걸리는 일반 불만 리뷰에도 이모지가
# 섞여 나오는 게 실사용 중 확인돼(2026-08-31) category != "no_issue" 전체로
# 넓혔다.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # 이모지·픽토그램(표정, 사물, 확장 심볼 등)
    "\U00002600-\U000026FF"  # 기타 심볼(☀☎ 등)
    "\U00002700-\U000027BF"  # 딩뱃(✅❌ 등)
    "\U0001F1E6-\U0001F1FF"  # 국기(지역 표시 문자)
    "\U00002B00-\U00002BFF"  # 화살표/별 등 기타 심볼(⭐ 등)
    "\U0000FE0F"              # variation selector-16 (이모지 표시 강제)
    "\U0000200D"              # zero-width joiner (복합 이모지 결합)
    "]+"
)


def _strip_emoji(text: str) -> str:
    without_emoji = _EMOJI_PATTERN.sub("", text)
    # 이모지 양옆에 공백이 있던 자리(예: "안녕 😊 하세요")가 이중 공백으로
    # 남는 것과, 줄 끝에 이모지만 있던 자리(예: "감사합니다😊\n")가 그대로
    # 남는 것을 정리한다. 문단을 나누는 줄바꿈 자체는 건드리지 않는다.
    collapsed = re.sub(r"[ \t]{2,}", " ", without_emoji)
    return re.sub(r"[ \t]+\n", "\n", collapsed).strip()

CATEGORY_LABELS = {
    "food_quality": "음식 품질(맛/온도/양)",
    "delivery": "배달(지연/파손)",
    "hygiene": "위생/이물질",
    "service": "응대",
    "price": "가격",
    "missing_or_wrong_item": "오배송/누락",
}


def _resolve_display_name(db: Session, store: Store, review: Review) -> str:
    """답글에서 "안녕하세요, {name}입니다"처럼 실제로 언급할 가게 이름을
    정한다. 한 배민 계정에 브랜드가 여러 개(치밥대장/블랙닭갈비/곱도리탕/
    행복가성비) 딸려있는데, 이전에는 항상 Store.name(단일 값, 대표
    브랜드 이름) 하나만 써서 리뷰가 다른 브랜드 것이어도 엉뚱한 브랜드명이
    답글에 들어갔다(2026-08-24 실측 확인 — "블랙닭갈비" 리뷰에 "치킨대장
    당고점입니다"가 붙음). review.platform_shop_no로 baemin_shop_brands를
    찾아 그 브랜드의 실제 이름을 쓰고, 못 찾으면(연결 정보 없음, 온보딩
    가상 리뷰 등) Store.name으로 폴백한다.

    baemin_shop_brands.shop_name은 배민 매장 선택 드롭다운 원문 그대로라
    "[음식배달] 블랙닭갈비 노원당고개점 / 고기·구이 14804914"처럼 프롬프트에
    쓰기엔 지저분하다 — 앞의 "[...]" 태그와 " / 카테고리 번호" 뒷부분을
    잘라 "블랙닭갈비 노원당고개점"만 남긴다."""
    if not review.platform_shop_no:
        return store.name

    brand = db.scalar(
        select(BaeminShopBrand)
        .join(StorePlatformConnection, BaeminShopBrand.connection_id == StorePlatformConnection.id)
        .where(
            StorePlatformConnection.store_id == store.id,
            BaeminShopBrand.shop_no == review.platform_shop_no,
        )
    )
    if brand is None:
        return store.name

    name = re.sub(r"^\[[^\]]*\]\s*", "", brand.shop_name)
    return name.split(" / ")[0].strip() or store.name


def _normalize_menu_name(name: str) -> str:
    """메뉴명 비교용 정규화. 리뷰의 menu_summary(주문 시점 표시명, 예:
    "[양념조절가능]숯불양념바베큐치킨")와 brand_menu_info.menu_items의
    등록명(예: "숯불양념바베큐치킨:")은 프로모션 태그·트레일링 콜론 등
    표기가 서로 달라 정확히 일치하지 않는다 — 대괄호 태그를 지우고
    앞뒤 공백/콜론을 정리해 느슨하게 비교한다."""
    name = re.sub(r"\[[^\]]*\]", "", name)
    return name.strip().rstrip(":").strip()


def _find_menu_context(db: Session, store: Store, review: Review) -> str | None:
    """리뷰의 실제 메뉴 구성/가게 소개 정보를 배민에서 가져온 그라운딩
    데이터(brand_menu_info)에서 찾는다. 원래 이 프로젝트엔 "메뉴" 데이터가
    전혀 없어서, AI가 리뷰 텍스트만 보고 메뉴 구성을 추측하다 틀린 답글을
    쓰는 문제가 실사용 중 확인됐다(2026-08-26 — "치킨마요는 밥만 많고
    고기가 없다"는 불만에 실제로는 정량대로 들어간 걸 사장님이 직접
    정정해야 했음). 연결 정보가 없거나(가게 미연결) 아직 메뉴 동기화 전
    이면(review_sync.py가 첫 실행 때 채움) None을 반환하고, 호출부는 이
    섹션을 그냥 생략한다 — 메뉴 그라운딩은 있으면 좋은 보강 정보지 필수
    전제가 아니다."""
    if not review.platform_shop_no:
        return None

    info = db.scalar(
        select(BrandMenuInfo)
        .join(StorePlatformConnection, BrandMenuInfo.connection_id == StorePlatformConnection.id)
        .where(
            StorePlatformConnection.store_id == store.id,
            BrandMenuInfo.shop_no == review.platform_shop_no,
        )
    )
    if info is None:
        return None

    lines = []
    if info.store_intro:
        lines.append(f"[가게 소개]\n{info.store_intro}")
    if info.food_origin:
        lines.append(f"[원산지]\n{info.food_origin}")
    if info.menu_intro:
        lines.append(f"[메뉴 소개]\n{info.menu_intro}")

    target = _normalize_menu_name(review.menu_summary or "")
    matched = None
    if target:
        for item in info.menu_items or []:
            item_name = _normalize_menu_name(item.get("name", ""))
            if item_name and (item_name in target or target in item_name):
                matched = item
                break
    if matched:
        detail = f"[고객이 주문한 메뉴: {matched['name']}]"
        if matched.get("composition"):
            detail += f"\n실제 구성: {matched['composition']}"
        if matched.get("desc"):
            detail += f"\n메뉴 설명: {matched['desc']}"
        lines.append(detail)

    return "\n\n".join(lines) if lines else None


def _build_system_prompt(display_name: str, style_rules: str, examples, tone_instruction: str, menu_context: str | None = None, *, strip_example_emoji: bool = False) -> str:
    def _example_reply(ex) -> str:
        return _strip_emoji(ex.reply_text) if strip_example_emoji else ex.reply_text

    example_block = "\n\n".join(
        f'예시 {i}: 리뷰 "{ex.review_text}" / 답글 "{_example_reply(ex)}"'
        for i, ex in enumerate(examples, start=1)
    ) if examples else "(아직 참고할 예시가 없습니다.)"

    menu_section = f"""

[가게/메뉴 실제 정보 — 사실 근거용]
아래는 배민에 등록된 이 가게의 실제 소개글과 메뉴 구성이다. 리뷰가 특정
메뉴나 재료를 언급하면 반드시 이 정보를 근거로 삼아라 — 실제 메뉴
구성과 다른 원인(예: "신메뉴라서", "양을 줄였다")을 추측해서 쓰지 마라.
여기 없는 내용(오늘 그 배치의 조리 상태 등)은 사장님만 아는 사실이니
지어내지 말고 일반적인 사과로 넘어가라.

{menu_context}""" if menu_context else ""

    return f"""너는 "{display_name}"의 사장님을 대신해 배달앱 리뷰에 답글을 쓴다.

[이 가게의 답글 스타일]
{style_rules}

[답글 톤]
{tone_instruction}
{menu_section}
[참고 예시 — 스타일 참고 전용]
아래는 이 가게 사장님이 실제로 쓴(또는 승인한) 답글 예시다.
**절대 지켜야 할 규칙**: 이 예시들은 말투·태도·구조(원인 설명 → 사과 →
재방문 유도)만 참고하라. 문장 내용을 그대로 복사하지 말고, 구체적 원인은
반드시 "이번 리뷰의 실제 상황"에만 근거해 새로 작성하라.

{example_block}

위 지시를 지켜 답글만 출력하고 다른 설명은 붙이지 마라."""


def _build_user_message(review: Review, category_label: str, repeat_count: int) -> str:
    lines = [f"별점: {review.rating}"]
    if review.category == "no_issue":
        # "불만 유형: no_issue"라고 그대로 넣으면 모델이 없는 불만을 억지로
        # 찾아 사과하게 될 수 있다 — 칭찬/무난 리뷰는 불만 프레이밍 자체를
        # 빼고, 리뷰에 실제로 담긴 요청·취향(예: "더 매웠으면")이 있으면
        # 그것만 자연스럽게 반영하도록 안내한다.
        lines.append("특이 불만 없음(칭찬 또는 중립적인 리뷰). 리뷰에 구체적인 취향/요청이 담겨있으면 자연스럽게 반영하고, 없으면 감사 인사 위주로 답하세요.")
    else:
        lines.append(f"불만 유형: {category_label}")
    lines.append(f'내용: "{review.content}"')
    lines.append(f"이 고객의 누적 주문 횟수: {review.customer_order_count}회")
    if review.customer_order_count > 1:
        lines.append("재방문 고객이니 자연스럽게 반영하세요.")
    if review.category != "no_issue" and repeat_count > 1:
        lines.append(f"이 유형 불만이 최근 30일간 {repeat_count}건째입니다 — 반복 문제임을 인지하되 변명처럼 들리지 않게 주의하세요.")
    if review.is_sensitive:
        lines.append("위생/안전 관련 민감 사안입니다. 섣부른 원인 추정이나 과도한 변명 없이, 진지하게 사과하고 구체적 조치(연락처 안내 등)를 제시하세요.")
    return "\n".join(lines)


def generate_ai_reply(db: Session, review: Review, store: Store, style: ReplyStyle) -> str:
    profile = db.scalar(select(StoreStyleProfile).where(StoreStyleProfile.store_id == store.id))
    style_rules = profile.rules if profile is not None else _FALLBACK_STYLE_RULES

    examples = fetch_golden_examples(db, store.id, review.category, review.content, limit=3)
    repeat_count = count_recent_same_category(db, store.id, review.category, days=30)
    category_label = CATEGORY_LABELS.get(review.category, review.category)

    tone_overridden = review.category != "no_issue" or review.is_sensitive or review.sentiment_conflict
    tone_instruction = _COMPLAINT_TONE_OVERRIDE if tone_overridden else style.tone_instruction

    display_name = _resolve_display_name(db, store, review)
    menu_context = _find_menu_context(db, store, review)
    system_prompt = _build_system_prompt(
        display_name, style_rules, examples, tone_instruction, menu_context,
        strip_example_emoji=tone_overridden,
    )
    user_message = _build_user_message(review, category_label, repeat_count)
    content = client.call_sonnet(system_prompt, user_message, max_tokens=800)
    return _strip_emoji(content) if tone_overridden else content
