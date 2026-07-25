# Delivery Review & Store Insight MVP

배달매장 사장을 위한 **DB 설계 중심** MVP. 완성형 서비스가 아니라 데이터 모델링,
현장 문제 이해, 범위 통제 능력을 보여주는 교육 과정 과제물이다.

리뷰닥터/세일즈랩을 벤치마크했지만 화면을 복제하지 않고 분석 후 재설계했다 —
기능은 참조하되 디자인(다크 테마 + 사이드바)은 새로 만들었다.

기술 스택: **PostgreSQL · FastAPI · Next.js**

## 절대 지키는 원칙

- 실제 배민/쿠팡이츠/요기요 API 연동, 실제 리뷰 크롤링, 실제 AI 호출, 실제 자동 답글 등록 **없음**
- 실제 CPC 자동 입찰, 실제 광고 순위 크롤링·스크린샷 판독 **없음**
- 실제 결제·구독 결제·자동 출금, 실제 문자/카카오톡 발송 **없음**
- 위 기능은 전부 Mock (seed 데이터 + 템플릿 응답)으로 흉내만 낸다
- **광고비율(ACoS) 계산만 실제 공식으로 계산한다** — `backend/app/acos.py`
- 개인정보 미저장: 전화번호는 `phone_hash`(SHA-256)로만 저장, 사업자번호·스토어
  아이디·주문번호는 전부 Mock 값, 주민번호·실명은 받지 않음

## DB 설계 (16개 테이블)

`users, stores, platforms, store_platform_connections, subscriptions, orders,
reviews, review_replies, reply_styles, reply_settings, daily_settlements,
repurchase_metrics, ad_campaigns, ad_performance_metrics, ad_rank_snapshots, alerts`

스키마 정본은 **`schema.sql`**(저장소 루트)이며 모든 FK에 `ON DELETE` 정책이 명시돼
있다. Mock 데이터는 **`seed.sql`**이 생성한다 (정합성 불변식 보장 — 매출·정산·재주문율은
전부 원본 테이블을 SQL로 집계해서 만들어짐). `backend/app/models.py`는 이 스키마를
SQLAlchemy로 1:1 미러링한 것이고, Alembic 마이그레이션은 쓰지 않는다.

## 실행

```bash
# 1. DB
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

브라우저에서 프론트 접속 후 **[데모 계정으로 로그인]** 버튼 한 번이면 바로 둘러볼 수
있다 (`demo@dris.kr` / `demo1234!`, seed된 매장 2곳 포함). 이메일 회원가입도 가능하며
가입 직후 빈 대시보드를 보여주지 않도록 기본 매장 1개가 자동 생성된다.

## 화면 (3개)

- **대시보드** — 매출/입금 기간 토글(오늘·1주·1개월·이번달), 답글 대기 리뷰, 재주문율,
  광고 성과(ACoS), 알림
- **리뷰 관리** — 답글 대기/검토중/완료 필터, 스타일 선택 → 템플릿 Mock 답글 생성 →
  수정 → 등록
- **광고 순위 모니터링** — 카테고리별 현재 순위·경쟁 예상 CPC·상태·추천 액션 (Mock
  스냅샷, 실제 크롤링 없음)

## 테스트

```bash
cd backend && .venv/bin/python -m pytest -v   # 31 passed (SQLite 인메모리)
cd frontend && npx tsc --noEmit
```

## 문서

- `CLAUDE.md` — 프로젝트 브리프 (범위, 절대 금지 목록, DB 설계 원칙, ACoS 공식, 작업 순서)
- `schema.sql` / `seed.sql` — DB 정본
