# 자동 답글 Pro 전용화 + 기존 미답변 리뷰 소급 처리 설계

## 배경

배민 자동 답글 실제 제출 기능(별점 5점 리뷰 한정, CLAUDE.md "배민 자동 답글
실제 제출" 절 참고)은 원래 "동기화 시점에 새로 발견된 리뷰"에만 적용되도록
설계돼 있었다 — `review_sync.py`가 `existing_ids`로 이미 아는
`external_review_id`는 건너뛰기 때문에, 자동답글을 켠 시점 이전에 이미 DB에
있던 미답변 리뷰는 앞으로 동기화가 몇 번을 더 돌아도 절대 자동으로 답글이
안 달린다.

실사용 중(2026-09-23~26) 사용자가 자동답글을 켰는데도 특정 리뷰 3건이 계속
"답글 대기"로 남아있어 원인을 조사했고, 위 설계가 의도한 동작임을 확인했다
— 다만 사용자는 이 동작을 원치 않았고("답변이 안된 리뷰도 전체로 써주게끔
해야할것 같아"), 동시에 자동답글 기능 자체를 Pro 전용으로 제한하기로
결정했다(대화 중 확정, 2026-09-26).

## 결정 사항 (브레인스토밍에서 확정)

1. **Pro 전용 범위**: 자동답글 기능 전체(신규 리뷰 자동 응답 + 아래 소급
   처리)를 Pro 전용으로 잠근다. Basic은 자동답글 토글 자체를 켤 수 없고,
   기존처럼 "AI 추천 답글" 버튼으로 하루 10건까지만 수동 생성 가능(변경
   없음).
2. **소급 처리 배치 방식**: 한 번에 전부 처리한다(개수 제한 없음). 리뷰가
   많이 쌓인 매장은 그만큼 그 동기화가 오래 걸릴 수 있다는 트레이드오프를
   인지하고 진행.
3. **이중답글 위험**: 소급 대상 리뷰가 그 사이 사장님이 배민 앱에서 직접
   답글을 달았을 가능성을 실시간으로 재확인하지 않는다 — DB의
   `status='unanswered'`를 그대로 신뢰한다. 알려진 한계로 문서에 남긴다
   (대상이 5점·no_issue·비민감 리뷰로 한정돼 있어 실제 피해는 낮다고 판단).

## 1. Pro 게이팅

### 1.1 설정 저장 시점 (`PUT /reply-settings`)

`auto_reply_enabled`를 `true`로 바꾸려는 요청인데 그 가게 소유자가 Pro가
아니면 거부한다. 기존 `require_pro_plan`(`app/plan.py`)과 동일한 응답
형태를 쓴다:

```python
if body.auto_reply_enabled and effective_plan(sub) != "pro":
    raise HTTPException(
        403,
        detail={"message": "자동 답글은 Pro 플랜 전용 기능입니다.", "error_code": "pro_required"},
    )
```

`sub`는 `select(Subscription).where(Subscription.user_id == user.id)`로
조회(이미 `require_pro_plan`이 쓰는 것과 동일한 조회 패턴). `false`로 끄는
요청은 플랜과 무관하게 항상 허용한다.

### 1.2 실행 시점 (`review_sync.py`, 최종 관문)

설정 저장 시점 검증만으로는 불충분하다 — Pro였을 때 켜둔 뒤 Basic으로
다운그레이드된 매장이 여전히 실제 배민 쓰기+Sonnet 호출을 계속 트리거할 수
있다. 실제 비용과 배민 계정 쓰기가 발생하는 지점이므로 여기서 다시 한 번
확인한다:

```python
store = db.get(Store, job.store_id)
sub = db.scalar(select(Subscription).where(Subscription.user_id == store.user_id))
is_pro = effective_plan(sub) == "pro"

reply_settings = db.scalar(select(ReplySetting).where(ReplySetting.store_id == job.store_id))
auto_reply_style = None
if reply_settings is not None and reply_settings.auto_reply_enabled and is_pro:
    auto_reply_style = db.get(ReplyStyle, reply_settings.style_id)
```

`is_pro` 계산은 매장별 루프(브랜드 루프) 밖, job 시작 시 한 번만 하면 된다
(플랜은 한 job 실행 도중 안 바뀐다고 가정).

### 1.3 프론트

`ReviewsRulesPage`(`/reviews/rules`)에서 Basic 사용자에게는 자동답글 토글을
잠금 표시(다른 Pro 전용 기능과 동일한 자물쇠 아이콘 패턴, `/account/billing`
링크)로 바꾼다. 백엔드만 막으면 화면상 켜지는 것처럼 보였다가 저장 시
403으로 실패하는 어색한 경험이 된다.

## 2. 기존 미답변 리뷰 소급 처리

### 2.1 어디에 넣을지

`review_sync.py`의 브랜드(shop_no) 루프 안, "새로 발견된 리뷰" INSERT 루프
바로 다음에 추가한다 — 새 리뷰 처리가 끝난 직후, 같은 shop_no 범위에서
과거 미답변 잔여를 훑는 흐름이다.

```python
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

게이트 조건(`rating>=_AUTO_REPLY_MIN_RATING_FLOOR`, `category=="no_issue"`,
`not is_sensitive`, `not sentiment_conflict`)은 신규 리뷰 경로와 완전히
동일한 상수/컬럼을 그대로 재사용한다 — 별도 기준을 새로 만들지 않는다.
golden_examples 승격도 신규 경로와 동일하게 하지 않는다(사람이 검토 안 한
순수 AI 산출물).

### 2.2 매번 도는 것에 대해

이 훑기는 매 동기화(수동/자동 스케줄러 무관)마다 무조건 실행된다 — "처음
한 번만" 특별 취급하지 않는다. 백로그가 한 번 비워지면 이후 동기화에서는
쿼리 결과가 거의 항상 빈 리스트라 사실상 공짜에 가까운 조회 한 번만
추가되는 구조라, 계속 켜둬도 부담이 없다는 게 이 설계를 단순하게 유지하는
근거다.

### 2.3 알려진 한계

- **이중답글 가능성**: 결정 사항 3번대로 실시간 재확인을 안 한다. 리뷰가
  5점·no_issue·비민감으로 한정된 만큼 실제 사고 시 파급력은 작다고 보지만,
  0은 아니다.
- **첫 실행 소요 시간**: 배포 직후 쌓여있던 백로그가 한꺼번에 처리되며,
  리뷰 1건당 배민 UI 조작(`submit_reply`)에 실측 10~20초가 걸린다 — 백로그가
  많은 매장은 그 동기화 자체가 평소보다 오래 걸릴 수 있다.
- **Basic 다운그레이드 체감**: 이번 변경으로 기존에 무료로 동작하던
  자동답글이 조용히 멈춘다(1.2절 게이트). 화면에는 Pro 전용 안내가 뜨지만,
  이미 켜둔 Basic 사용자 입장에서는 "됐었는데 안 된다"로 느껴질 수 있다.

## 3. 그 외 자잘한 정리

`backend/app/routers/reply_settings.py` 상단 docstring의 "자동 답글은
Mock이다 — auto_reply_enabled를 켜도 실제로 답글이 자동 등록되지 않는다"는
문장은 실제 자동 제출 기능이 붙은 뒤(2026-08-25) 갱신되지 않은 오래된
설명이다 — 이번 작업에서 같이 고친다.

## 4. 테스트 범위

- `test_reply_settings.py`: Basic이 `auto_reply_enabled=true`로 PUT 시도 →
  403 + `pro_required`. Pro는 정상 통과. `false`로 끄는 요청은 플랜 무관
  항상 허용.
- `test_review_sync.py`: (a) Basic 매장은 신규 리뷰가 조건을 만족해도
  `auto_reply_style`이 None이라 자동답글 안 탐(기존 없는 회귀 케이스), (b)
  Pro 매장에서 기존 미답변 리뷰(조건 만족)가 소급으로 답글이 달리고
  `status`가 `answered`로 바뀜, (c) 조건 불만족(다른 카테고리/민감/낮은
  별점) 미답변 리뷰는 소급 대상에서 제외됨, (d) 소급 처리 중 하나가
  실패해도 나머지 리뷰 처리와 job 전체가 막히지 않고
  `job.error_message`에 남음(기존 `auto_reply_errors` 패턴 재사용).
