# 리뷰닥터 벤치마크 MVP (review-doctor-mockup)

배달매장 사장의 3대 현장 문제를 Mock 데이터로 시연하는 **DB 설계 중심** 프로토타입.
리뷰 답글 노동 / 매출·입금 차액 / 광고 순위 밀림.

외부 API·크롤링·자동입찰·LLM 없음. 데이터는 seed, 답글은 템플릿, 순위는 시계열 스냅샷.

## 실행

```bash
# 1. DB
docker compose up -d db

# 2. 백엔드 (스키마 + seed + 서버)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m app.seed.run
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8000

# 3. 프론트
cd frontend
npm install
npm run dev                               # http://localhost:3000
```

## 테스트

```bash
cd backend && .venv/bin/python -m pytest -v
```

## 설계 문서

- 스펙: `docs/superpowers/specs/2026-07-25-review-doctor-mvp-design.md` (테이블 16개 ERD·범위 결정 기록 포함)
- 구현 계획: `docs/superpowers/plans/2026-07-25-review-doctor-mvp.md`
