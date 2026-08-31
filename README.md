# 스토어 타겟 (Store Target)

> Delivery Review & Store Insight — 배달매장 사장을 위한 리뷰·매출·광고 관리 SaaS

원래는 **DB 설계 중심 교육 과정 과제물**로 시작했다(Mock 데이터/Mock API만
사용). 이후 외주 포트폴리오용으로 실제 동작하는 데모가 필요해져서, 카카오
로그인 → 결제 → 실제 배민 데이터 연동 → LLM 기반 답글 생성 순서로 실 SaaS
기능을 단계적으로 붙였다. 지금은 아래 "실제로 동작하는 기능" 항목들이 전부
진짜로 동작한다 — Mock으로 남아있는 범위는 그 아래 별도로 명시했다.

리뷰닥터/세일즈랩을 벤치마크했지만 화면을 복제하지 않고 분석 후 재설계했다.
같은 백엔드를 쓰는 React Native 모바일 앱도 별도 저장소로 함께 개발 중이다 —
[store-target-app](https://github.com/imiter/store-target-app).

## 기술 스택

**PostgreSQL(+ pgvector) · FastAPI · Next.js · React Native**

- **LLM**: Anthropic Claude(Haiku — 리뷰 분류, Sonnet — RAG 답글 생성),
  Voyage AI(`voyage-4`, 골든 예시 임베딩)
- **실 스크래핑**: Playwright(배민 사장님광장 로그인·리뷰·매출·정산·메뉴),
  Appium(광고 순위 반경별 실측, 실기기/에뮬레이터)
- **결제**: 토스페이먼츠(테스트 키)
- **소셜 로그인**: 카카오
- **이메일 발송**: Resend

## 실제로 동작하는 기능 (Mock 아님)

- **로그인**: 이메일 회원가입(Resend로 인증 코드 실발송) + 카카오 소셜 로그인
- **배민 데이터 연동**: 실계정 로그인(봉 탐지 우회 포함) 후 리뷰·매출·입금·
  재주문율·주문내역·정산 상세(수수료/배달비/고객할인/광고비)·우리가게클릭
  광고 성과·메뉴 정보를 실제로 스크래핑해 DB에 적재. "가게 연결" 화면의
  버튼으로 수동 동기화하거나, 매일 KST 04시 자동 스케줄러가 동기화한다.
- **LLM 기반 답글 생성 (RAG)**: 리뷰가 동기화되는 시점에 Haiku가 불만
  유형·민감도·별점-내용 불일치를 분류하고, 답글 생성 시 Sonnet이 이
  매장의 진짜 과거 답글(golden examples, pgvector 코사인 유사도 검색)과
  학습된 매장 말투를 few-shot으로 반영해 작성한다. 사장님이 직접 쓰거나
  수정한 답글은 자동으로 새 골든 예시로 승격된다.
  민감/불만 리뷰는 페르소나와 무관하게 이모지 없는 차분한 톤으로 강제
  전환된다.
- **5점 리뷰 자동 답글 실제 제출**: 별점 5점이면서 불만 신호가 없는(민감
  아님·별점-내용 불일치 아님) 리뷰에 한해, AI가 생성한 답글을 실제
  배민에 자동으로 등록한다(설정에서 on/off, 최소 별점 5점 하한 고정).
- **광고 순위 반경별 실측**: 가게 기준 거리(0km/1.5~2.5km/2.5~3.5km)별
  카테고리 순위를 실기기/에뮬레이터로 직접 스크롤하며 실측한다("우리가게
  순위 확인" 버튼). 우리가게클릭 CPC 조정값도 실제로 배민에 반영할 수
  있다.
- **결제/구독**: 토스페이먼츠 테스트 키로 Basic → Pro 실제 결제(카드/
  계좌이체/간편결제, 가상계좌 입금 대기 포함). Basic/Pro 플랜에 따라
  답글 생성 일일 한도와 광고 순위 모니터링 접근이 실제로 갈린다.
- **ACoS(광고비율) 계산**: 실제 공식으로 계산 — `backend/app/acos.py`

## 여전히 Mock인 기능

- 쿠팡이츠/요기요 실 연동 전체 (배민만 실연동)
- 실제 CPC **자동** 입찰(값을 수동으로 입력해 "적용하기"를 누르면 실제
  반영은 되지만, 알고리즘이 스스로 입찰가를 정하는 자동화는 없음)
- 실제 문자(SMS) 발송
- 복잡한 다중 사업자 권한 관리 (사장 1명 = 로그인 1개 기준)

각 기능이 "왜 지금은 이 범위인지"는 `CLAUDE.md`에 결정 시점·경위와 함께
기록돼 있다.

## 개인정보 원칙

전화번호는 원문 대신 `phone_hash`로만 저장, 사업자번호·주문번호는 전부 Mock
값, 주민번호·실명은 받지 않는다.

## DB 설계 (26개 테이블)

```
users, stores, platforms, store_platform_connections, subscriptions,
orders, reviews, review_replies, reply_styles, reply_settings,
daily_settlements, repurchase_metrics, ad_campaigns, ad_performance_metrics,
ad_rank_snapshots, alerts, social_accounts, signup_verifications,
golden_examples, store_style_profile, review_sync_jobs, baemin_shop_brands,
brand_ad_click_metrics, payments, onboarding_scenarios, brand_menu_info
```

스키마 정본은 **`schema.sql`**(저장소 루트)이며 모든 FK에 `ON DELETE` 정책이
명시돼 있다. Mock 데이터는 **`seed.sql`**이 생성한다. `backend/app/models.py`는
이 스키마를 SQLAlchemy로 1:1 미러링하고, Alembic 마이그레이션은 쓰지 않는다.
`golden_examples.embedding`은 pgvector `vector(1024)` 타입이라 로컬 DB도
`pgvector/pgvector:pg16` 이미지가 필요하다.

## 실행

```bash
# 1. DB (pgvector 확장 포함 이미지 필요)
docker compose up -d db
docker compose exec -T db psql -U postgres -c "CREATE DATABASE delivery_insight;"
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < schema.sql
docker compose exec -T db psql -U postgres -d delivery_insight -f /dev/stdin < seed.sql

# 2. 백엔드
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/delivery_insight" \
  .venv/bin/uvicorn app.main:app --reload   # http://localhost:8000

# 3. 프론트
cd ../frontend
npm install
npm run dev                                 # http://localhost:3000 (포트 사용 중이면 자동으로 다음 포트)
```

로그인 화면의 **[데모 계정으로 로그인]** 버튼 한 번이면 바로 둘러볼 수 있다
(이메일 `demo@dris.kr`, seed된 매장 포함 — 비밀번호는 버튼이 자동으로
채운다). 이메일 회원가입도 가능하며(Resend 실발송) 가입 직후 빈 대시보드를
보여주지 않도록 기본 매장 1개가 자동 생성된다.

카카오 로그인, 배민 실연동, LLM 답글 생성, 토스 결제까지 전부 로컬에서
동작시키려면 각 기능에 필요한 API 키를 `backend/.env`(카카오 시크릿,
`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `TOSS_SECRET_KEY`,
`RESEND_API_KEY`, `CREDENTIAL_ENCRYPTION_KEY`)와 `frontend/.env.local`
(`NEXT_PUBLIC_KAKAO_CLIENT_ID`, `NEXT_PUBLIC_TOSS_CLIENT_KEY`)에 채워야
한다 — 값이 없어도 이메일 로그인과 데모 계정 로그인, Mock 기반 화면은
정상 동작한다. 각 변수의 의미는 `.env.example` 파일들의 주석을 참고한다.
배민 실연동(리뷰/매출/광고 스크래핑)은 별도로 `crawler/README.md`의 워커
설정이 필요하다.

## 화면

- **대시보드** — 매출/입금 기간 토글, 우가클 점수·재주문율(링 게이지),
  답글 대기 리뷰, 알림
- **리뷰 관리** — 답글 대기/검토중/완료 필터, RAG 기반 AI 추천 답글 생성 →
  수정 → 등록, 답글 규칙/스타일 설정
- **매출** — 일별 매출·입금 추이, 정산 상세(수수료/배달비/고객할인/광고비),
  주문내역
- **광고 순위 모니터링** — 카테고리별 현재 순위·추천 액션 + 가게 기준
  반경별 실측 순위("우리가게 순위 확인" 버튼) + 우리가게클릭 광고 성과
  (링 게이지/임계값 바)
- **내 정보 관리** — 가게 연결(배민 계정 연결·데이터 동기화), 계정 관리,
  구독 관리(Basic/Pro, 토스 결제)

## 테스트

```bash
cd backend && .venv/bin/python -m pytest -v        # 557 passed (SQLite 인메모리)
cd frontend && npx tsc --noEmit
cd crawler && .venv/bin/python -m pytest -v         # 35 passed
```

pgvector 벡터 검색(`ORDER BY embedding <->`)은 SQLite로 재현할 수 없어
`backend/tests/test_llm_rag_pgvector.py` 하나만 로컬 Postgres(pgvector 설치)가
있을 때만 실행되고, 없으면 자동 스킵된다.

## 문서

- `CLAUDE.md` — 프로젝트 브리프. 범위, 실 SaaS 전환 경위, 각 기능이 언제·왜
  Mock에서 실제로 바뀌었는지가 결정 시점 순서대로 전부 기록돼 있다
- `schema.sql` / `seed.sql` — DB 정본
- `docs/schema.dbml` — ERD용 DBML. [dbdiagram.io](https://dbdiagram.io/d)에
  통째로 붙여넣으면 다이어그램이 그려진다
- `docs/superpowers/specs/` — 기능별 설계 문서(브레인스토밍 결과) 아카이브
- `crawler/README.md` — 배민 실연동 크롤 워커(Playwright/Appium) 설정/실행
  방법
