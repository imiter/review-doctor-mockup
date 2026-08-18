# 광고 순위 모니터링 — 4브랜드 실데이터 CPC + 입찰 조정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "광고 순위 모니터링" 화면을 4개 실브랜드(치밥대장/곱도리탕/블랙닭갈비/행복가성비) 전부 실데이터로 확장한다. "현재 CPC"는 배민 실제 입찰가로 갱신되고, "경쟁 예상 CPC"와 "Mock 스냅샷" 문구는 화면에서 사라지며, 사용자가 브랜드별로 목표 순위와 시도할 CPC 금액을 직접 입력해 "적용하기"를 누르면 배민에 실제 반영 후 순위를 자동 재측정하고 다음 시도 금액을 추천해준다(완전 자동 입찰 없음).

**Architecture:** 기존에도 `ad_campaigns`/`ad_rank_snapshots`는 캠페인 여러 개(브랜드 여러 개)를 이미 배열로 다루는 구조였다 — `GET /ads/rank-monitoring`, `GET /ads/rank-by-distance`, `POST /ads/rank-by-distance/run`은 전부 `store_id`의 모든 캠페인을 순회해 리스트로 반환/처리하고, 프론트엔드도 이미 그 배열을 `.map()`으로 그려 브랜드마다 한 행/블록을 보여준다(**그라운딩 조사로 확인 — 최초 설계 문서가 "선택 UI가 필요하다"고 가정했던 부분은 이미 구조적으로 해결돼 있었다. 아래 Global Constraints 참고**). 그래서 "4브랜드 지원"의 실제 남은 작업은 (1) 3개 브랜드의 `ad_campaigns` 행 추가, (2) 배민의 실제 CPC 입찰가(`/v4/cpc/bookings/by-shop-number`)를 읽어와 `current_cpc`를 갱신하는 스크레이퍼 + 동기화 연동, (3) 사용자가 직접 실제 입찰가를 반영하고 순위를 재측정하는 쓰기 스크레이퍼 + 엔드포인트, (4) 화면에서 Mock/경쟁CPC 관련 문구·컬럼 제거 + 브랜드별 입찰 조정 UI 추가다.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql), Playwright(sync API, backend), Next.js App Router(TypeScript, App Router `(app)` 세그먼트).

## Global Constraints

- **선택 UI를 새로 만들지 않는다.** 설계 문서(`docs/superpowers/specs/2026-08-18-ad-rank-multibrand-cpc-design.md`)는 "브랜드 4개를 탭/드롭다운으로 선택하는 UI"를 제안했지만, 이 계획 수립 중 `frontend/src/app/(app)/ads/page.tsx`를 직접 읽어 확인한 결과 세 카드(순위 현황/반경별 실측 순위/광고 성과) 전부 이미 캠페인 배열을 `.map()`해서 브랜드마다 한 행/블록을 보여주고 있었다 — 캠페인 3개만 더 생기면 별도 UI 없이도 4개 브랜드가 전부 화면에 나타난다. 이 계획은 그 기존 구조를 그대로 활용하고, 목표순위/입찰금액 입력은 각 브랜드 행/블록 안에 인라인으로 추가한다(아래 Task 7). 이 판단이 틀렸다고 실행 중 판명되면(예: 4행이 너무 길어 실사용이 어렵다는 피드백) 사용자에게 먼저 확인한다.
- `ad_campaigns`/`ad_rank_snapshots`에 스키마 변경이 있을 때는 `schema.sql`의 `CREATE TABLE` 문 자체를 수정한다(이 프로젝트는 Alembic을 쓰지 않는다 — `schema.sql`이 정본).
- 로컬 Postgres에는 `psql` CLI가 없다 — DB에 직접 쓰거나 읽어야 하는 단계는 전부 `backend/.venv/bin/python -c "..."` + `psycopg`를 쓴다(`DATABASE_URL`은 `postgresql+psycopg://`로 시작하므로 `psycopg.connect()`에는 `postgresql://`로 바꿔서 넘겨야 한다).
- 백엔드 서버를 재시작할 때는 반드시 `cd backend && set -a && source .env && set +a && uvicorn app.main:app --reload` 처럼 `.env`를 먼저 source한다(`app/db.py`의 기본 `DATABASE_URL`은 포트 5432를 가리켜 로컬 Postgres 포트 15432와 다르다 — 안 하면 조용히 잘못된 DB에 붙는다).
- Playwright 기반 스크레이퍼 함수(`fetch_cpc_booking`, `submit_cpc_bid`) 자체는 이 저장소 컨벤션대로 자동화된 pytest로 덮지 않는다(`backend/tests/test_baemin_ads.py`의 기존 주석 참고 — "Playwright가 필요해 pytest로 못 덮지만"). 순수 판정/파싱 로직만 분리해서 테스트한다.
- **Task 6(쓰기 스크레이퍼 라이브 캡처)은 subagent에게 위임하지 않는다.** 사용자의 실제 배민 광고 계정에 진짜 금액을 반영하는 첫 쓰기 액션이라, 이 태스크의 Step 1은 orchestrator(메인 세션)가 사용자에게 채팅으로 실시간 동의를 구한 뒤 직접 수행한다. 이 프로젝트에서 지금까지 승인된 모든 "절대 금지 예외"는 읽기 전용이었다(로그인/리뷰/매출/순위 크롤링) — CPC 쓰기는 이 세션 최초의 쓰기 액션이므로 동일한 신뢰 수준으로 자동 위임하면 안 된다. 실제 요청/응답 형태가 확인된 뒤(Step 1 완료 후)부터는 일반 implementer subagent에게 위임 가능하다.
- 완전 자동 반복 입찰(목표 도달까지 무인 루프)은 스코프 밖이다 — 매 "적용"은 사용자가 직접 클릭해야 한다(설계 문서 "입찰 자동화 범위에 대한 논의" 절, `CLAUDE.md`의 "절대 금지: 실제 CPC 자동 입찰").
- 배민의 "스마트 모드"(자동 최적화) 연동은 스코프 밖 — 항상 "수동 설정 모드"만 다룬다.
- CPC 인하(다운) 추천은 스코프 밖 — 목표 미달 시 증액 추천만 다룬다.
- 4개 브랜드 동시 순위 재측정은 스코프 밖 — 크롤은 기존 `_crawl_lock` 제약대로 에뮬레이터 하나에서 순차 실행만 가능하다.
- 참고 스펙: `docs/superpowers/specs/2026-08-18-ad-rank-multibrand-cpc-design.md`
- 참고 이전 계획(같은 기능 영역, 스타일 참고용): `docs/superpowers/plans/2026-08-15-baemin-ad-rank-real-brand.md`

---

### Task 1: 데이터 모델 — `ad_rank_snapshots.bid_at_snapshot` 컬럼 추가

**Files:**
- Modify: `schema.sql` (`ad_rank_snapshots` CREATE TABLE 블록)
- Modify: `backend/app/models.py` (`AdRankSnapshot` 모델)
- Modify: `backend/scripts/ingest_rank_snapshots.py` (`ingest()` 함수)
- Test: `backend/tests/test_ingest_rank_snapshots.py` (신규 파일)

**Interfaces:**
- Consumes: 없음.
- Produces: `AdRankSnapshot.bid_at_snapshot: int | None`(신규 필드) — 실측 당시 `ad_campaigns.current_cpc` 스냅샷. 이후 태스크는 이 필드를 API 응답에 노출하지 않는다(스코프 밖, 이력 기록 목적만).

- [ ] **Step 1: `schema.sql` — `ad_rank_snapshots` 블록에 컬럼 추가**

`schema.sql`에서 다음 블록을 찾는다(15번 테이블):

```sql
CREATE TABLE ad_rank_snapshots (
    id                 BIGSERIAL PRIMARY KEY,
    campaign_id        BIGINT      NOT NULL REFERENCES ad_campaigns(id) ON DELETE CASCADE,
    snapshot_at        TIMESTAMPTZ NOT NULL,
    current_rank       SMALLINT    NOT NULL CHECK (current_rank >= 1),
    competitor_est_cpc INT         CHECK (competitor_est_cpc >= 0),  -- 경쟁 가게 예상 CPC (Mock, 시간별 스냅샷 전용)
    status             VARCHAR(12) CHECK (status IN ('normal', 'rank_dropped')),
    recommended_action VARCHAR(10) NOT NULL DEFAULT 'keep'
                       CHECK (recommended_action IN ('keep', 'raise_cpc', 'lower_cpc')),
    suggested_cpc      INT         CHECK (suggested_cpc >= 0),
    distance_km        NUMERIC(4,2) CHECK (distance_km >= 0),  -- NULL=시간별 Mock, 값 있으면 반경 실측(crawler/)
    point_label        VARCHAR(20),                            -- '0km', '1.5~2.5km' 등 화면 표시용 (실측 전용)
    total_scanned      SMALLINT    CHECK (total_scanned >= 0), -- 실측 시 스캔된 리스트 항목 수
    ads_above          SMALLINT    CHECK (ads_above >= 0),     -- 실측 시 내 가게보다 위에 있던 광고 개수
    UNIQUE (campaign_id, snapshot_at)
);
```

다음으로 교체(`ads_above` 다음 줄에 `bid_at_snapshot` 추가):

```sql
CREATE TABLE ad_rank_snapshots (
    id                 BIGSERIAL PRIMARY KEY,
    campaign_id        BIGINT      NOT NULL REFERENCES ad_campaigns(id) ON DELETE CASCADE,
    snapshot_at        TIMESTAMPTZ NOT NULL,
    current_rank       SMALLINT    NOT NULL CHECK (current_rank >= 1),
    competitor_est_cpc INT         CHECK (competitor_est_cpc >= 0),  -- 경쟁 가게 예상 CPC (Mock, 시간별 스냅샷 전용)
    status             VARCHAR(12) CHECK (status IN ('normal', 'rank_dropped')),
    recommended_action VARCHAR(10) NOT NULL DEFAULT 'keep'
                       CHECK (recommended_action IN ('keep', 'raise_cpc', 'lower_cpc')),
    suggested_cpc      INT         CHECK (suggested_cpc >= 0),
    distance_km        NUMERIC(4,2) CHECK (distance_km >= 0),  -- NULL=시간별 Mock, 값 있으면 반경 실측(crawler/)
    point_label        VARCHAR(20),                            -- '0km', '1.5~2.5km' 등 화면 표시용 (실측 전용)
    total_scanned      SMALLINT    CHECK (total_scanned >= 0), -- 실측 시 스캔된 리스트 항목 수
    ads_above          SMALLINT    CHECK (ads_above >= 0),     -- 실측 시 내 가게보다 위에 있던 광고 개수
    bid_at_snapshot    INT         CHECK (bid_at_snapshot >= 0), -- 실측 당시 ad_campaigns.current_cpc 스냅샷(이력/추천 근거용)
    UNIQUE (campaign_id, snapshot_at)
);
```

- [ ] **Step 2: `backend/app/models.py` — `AdRankSnapshot`에 필드 추가**

`class AdRankSnapshot` 블록의 `ads_above: Mapped[int | None]` 줄 다음에 삽입:

```python
    bid_at_snapshot: Mapped[int | None]
```

- [ ] **Step 3: 실패하는 테스트 작성**

`backend/tests/test_ingest_rank_snapshots.py` 신규 생성:

```python
import csv
import pathlib

from sqlalchemy.orm import sessionmaker

import scripts.ingest_rank_snapshots as ingest_module
from app.models import AdCampaign, AdRankSnapshot


def _write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    fieldnames = ["timestamp", "rank", "distance_km", "point_label", "total_scanned", "ads_above"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_ingest_records_bid_at_snapshot_from_campaign_current_cpc(db_session, seeded_user, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_module, "SessionLocal", sessionmaker(bind=db_session.get_bind(), autoflush=False))
    campaign = AdCampaign(
        store_id=seeded_user["store"].id, category="치킨", current_cpc=125, target_rank=3,
        status="active", shop_no="14804318",
    )
    db_session.add(campaign)
    db_session.commit()

    csv_path = tmp_path / "results.csv"
    _write_csv(csv_path, [
        {"timestamp": "2026-08-18T09:00:00", "rank": "5", "distance_km": "0",
         "point_label": "0km", "total_scanned": "20", "ads_above": "2"},
    ])

    inserted, skipped = ingest_module.ingest(csv_path, campaign.id)

    assert (inserted, skipped) == (1, 0)
    snapshot = db_session.query(AdRankSnapshot).filter_by(campaign_id=campaign.id).one()
    assert snapshot.bid_at_snapshot == 125


def test_ingest_skips_unparseable_rank_without_bid_at_snapshot(db_session, seeded_user, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_module, "SessionLocal", sessionmaker(bind=db_session.get_bind(), autoflush=False))
    campaign = AdCampaign(
        store_id=seeded_user["store"].id, category="치킨", current_cpc=125, target_rank=3,
        status="active", shop_no="14804318",
    )
    db_session.add(campaign)
    db_session.commit()

    csv_path = tmp_path / "results.csv"
    _write_csv(csv_path, [
        {"timestamp": "2026-08-18T09:00:00", "rank": "NOT_FOUND", "distance_km": "0",
         "point_label": "0km", "total_scanned": "20", "ads_above": "2"},
    ])

    inserted, skipped = ingest_module.ingest(csv_path, campaign.id)

    assert (inserted, skipped) == (0, 1)
    assert db_session.query(AdRankSnapshot).filter_by(campaign_id=campaign.id).count() == 0
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ingest_rank_snapshots.py -v`
Expected: FAIL — `AttributeError: module 'scripts.ingest_rank_snapshots' has no attribute 'SessionLocal'` 아님, 실제로는 `snapshot.bid_at_snapshot == 125` 단언에서 `AttributeError: 'AdRankSnapshot' object has no attribute 'bid_at_snapshot'` (Step 2를 먼저 적용했다면 컬럼은 있지만 `ingest()`가 채우지 않아 `None == 125`로 실패). 둘 중 하나로 실패해야 한다 — 통과하면 잘못된 것이다.

- [ ] **Step 5: `backend/scripts/ingest_rank_snapshots.py` — `bid_at_snapshot` 채우기**

`ingest()` 함수 안의 `AdRankSnapshot(...)` 생성 블록:

```python
                snapshot = AdRankSnapshot(
                    campaign_id=campaign.id,
                    snapshot_at=datetime.datetime.fromisoformat(row["timestamp"]).replace(tzinfo=_KST),
                    current_rank=rank,
                    status="rank_dropped" if rank > campaign.target_rank else "normal",
                    distance_km=row["distance_km"] or None,
                    point_label=row["point_label"],
                    total_scanned=int(row["total_scanned"]),
                    ads_above=int(row["ads_above"]),
                )
```

다음으로 교체:

```python
                snapshot = AdRankSnapshot(
                    campaign_id=campaign.id,
                    snapshot_at=datetime.datetime.fromisoformat(row["timestamp"]).replace(tzinfo=_KST),
                    current_rank=rank,
                    status="rank_dropped" if rank > campaign.target_rank else "normal",
                    distance_km=row["distance_km"] or None,
                    point_label=row["point_label"],
                    total_scanned=int(row["total_scanned"]),
                    ads_above=int(row["ads_above"]),
                    bid_at_snapshot=campaign.current_cpc,
                )
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ingest_rank_snapshots.py -v`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 7: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 8: 로컬 DB에 스키마 재적용**

```bash
cd backend && set -a && source .env && set +a && .venv/bin/python -c "
import psycopg, os
url = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
conn = psycopg.connect(url)
cur = conn.cursor()
cur.execute('ALTER TABLE ad_rank_snapshots ADD COLUMN IF NOT EXISTS bid_at_snapshot INT CHECK (bid_at_snapshot >= 0)')
conn.commit()
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='ad_rank_snapshots' ORDER BY ordinal_position\")
print([r[0] for r in cur.fetchall()])
"
```

Expected: 컬럼 목록 마지막에 `bid_at_snapshot`이 보인다.

- [ ] **Step 9: 커밋**

```bash
git add schema.sql backend/app/models.py backend/scripts/ingest_rank_snapshots.py backend/tests/test_ingest_rank_snapshots.py
git commit -m "feat: ad_rank_snapshots에 실측 당시 CPC를 기록하는 bid_at_snapshot 컬럼 추가"
```

---

### Task 2: 3개 신규 브랜드 `ad_campaigns` 행 추가

**Files:**
- Modify: `seed.sql` (`ad_campaigns` INSERT 블록)
- Test: `backend/tests/test_ads.py` (기존 테스트로 회귀 확인, 신규 테스트 불필요 — 순수 데이터 추가)

**Interfaces:**
- Consumes: 없음.
- Produces: `store_id=1`에 `shop_no` 있는 `ad_campaigns` 행 4개(기존 치킨/치밥대장 1개 + 이 태스크의 3개). 이후 태스크(3, 4, 5, 6, 7)는 이 4개 행이 실존한다고 가정한다.

이 태스크는 로컬 검증 DB에 이미 존재하는 실 계정 데이터(`store_id=1`, `store_platform_connections.id=6`)에 대한 것이다. `baemin_shop_brands`(`connection_id=6`)에 이미 4개 브랜드가 다음과 같이 들어있음을 확인했다(재조사 불필요):

| shop_no | shop_name | 카테고리(파싱됨) |
|---|---|---|
| 14804318 | 치밥대장 숯불양념92치킨 노원당고개점 | 치킨 (이미 `ad_campaigns.id=1`로 존재) |
| 14804912 | 곱도리탕 진짜 잘하는집 노원당고개점 | 찜·탕·찌개 |
| 14804914 | 블랙닭갈비 노원당고개점 | 고기·구이 |
| 14805005 | 행복가성비 컵밥&우동 노원당고개점 | 백반·죽·국수 |

`current_cpc`/`target_rank`는 Task 4(데이터 동기화 CPC 연동)가 첫 실행되면 즉시 실측값으로 덮어써지므로, 지금은 임시값(`current_cpc=100`, `target_rank=10`)으로 채운다.

- [ ] **Step 1: `seed.sql` — `ad_campaigns` INSERT 블록에 3행 추가**

`seed.sql`에서 다음 블록을 찾는다:

```sql
INSERT INTO ad_campaigns (store_id, category, current_cpc, target_rank, status, shop_no) VALUES
(1, '치킨',   400, 3, 'active', '14804318'),
(2, '닭갈비', 300, 5, 'active', NULL);
```

다음으로 교체:

```sql
INSERT INTO ad_campaigns (store_id, category, current_cpc, target_rank, status, shop_no) VALUES
(1, '치킨',       400, 3,  'active', '14804318'),
(1, '찜·탕·찌개',  100, 10, 'active', '14804912'),
(1, '고기·구이',   100, 10, 'active', '14804914'),
(1, '백반·죽·국수', 100, 10, 'active', '14805005'),
(2, '닭갈비',      300, 5,  'active', NULL);
```

- [ ] **Step 2: 로컬 검증 DB에 3행 직접 INSERT**

`seed.sql`은 새 DB를 처음 초기화할 때만 실행되므로, 이미 떠 있는 로컬 검증 DB에는 별도로 넣어야 한다:

```bash
cd backend && set -a && source .env && set +a && .venv/bin/python -c "
import psycopg, os
url = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
conn = psycopg.connect(url)
cur = conn.cursor()
cur.execute('''
    INSERT INTO ad_campaigns (store_id, category, current_cpc, target_rank, status, shop_no)
    SELECT 1, category, 100, 10, 'active', shop_no FROM (VALUES
        ('찜·탕·찌개', '14804912'),
        ('고기·구이', '14804914'),
        ('백반·죽·국수', '14805005')
    ) AS v(category, shop_no)
    WHERE NOT EXISTS (SELECT 1 FROM ad_campaigns WHERE shop_no = v.shop_no)
''')
conn.commit()
cur.execute('SELECT id, store_id, category, current_cpc, target_rank, shop_no FROM ad_campaigns ORDER BY id')
for row in cur.fetchall():
    print(row)
"
```

Expected: `store_id=1`에 `shop_no`가 `14804318`/`14804912`/`14804914`/`14805005`인 행 4개가 보인다(`WHERE NOT EXISTS`라 재실행해도 중복 삽입되지 않는다).

- [ ] **Step 3: 전체 백엔드 테스트로 회귀 확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS — `seed.sql` 변경은 프로덕션/로컬 신규 초기화 시에만 영향을 주고, pytest는 테스트 전용 스키마를 쓰므로 영향 없음을 확인하는 차원.

- [ ] **Step 4: 커밋**

```bash
git add seed.sql
git commit -m "feat: 4브랜드 전체(곱도리탕/블랙닭갈비/행복가성비 추가) ad_campaigns seed 반영"
```

---

### Task 3: `fetch_cpc_booking` 읽기 스크레이퍼

**Files:**
- Modify: `backend/scrapers/baemin_ads.py`

**Interfaces:**
- Consumes: `page`(Playwright `Page`, 로그인된 배민 세션), `shop_no: str`.
- Produces: `fetch_cpc_booking(page, shop_no: str) -> dict` — `{"bid": int, "max_bid": int, "monthly_budget": int, "spent_budget": int, "is_auto_bidding": bool}`. Task 4가 `bid` 필드를 `ad_campaigns.current_cpc`에 그대로 대입한다.

실측(2026-08-18, 치밥대장 shop_no=14804318)으로 확인한 실제 응답 형태:
```json
{"shopNumber": 14804318, "monthlyBudget": 1000000, "spentBudget": 150065, "bid": 95, "maxBid": 860, "shopName": "...", "serviceTypes": [...], "cpcDisplayRadius": ..., "cpcDisplayRadiusDetails": [...], "isAutoBidding": false, "isFirstBeginBadge": false}
```

- [ ] **Step 1: `backend/scrapers/baemin_ads.py` 끝에 함수 추가**

```python
def fetch_cpc_booking(page, shop_no: str) -> dict:
    """사장님광장 "광고·서비스관리" 화면(`/shops/{shop_no}/ad/campaign`)에서
    `GET /v4/cpc/bookings/by-shop-number?shopNumber={shop_no}` organic 응답을
    가로채 현재 CPC 입찰가 등을 반환한다. `fetch_shop_info`(baemin_stats.py)와
    동일한 단발성 GET 인터셉트 패턴 — 화면 진입만으로 호출되는 API라
    `fetch_brand_click_metrics`처럼 명시적 상호작용을 기다릴 필요가 없다.

    반환 키: `bid`(int, 클릭당 희망 광고금액=현재 CPC), `max_bid`(int),
    `monthly_budget`(int), `spent_budget`(int), `is_auto_bidding`(bool).
    """
    state = {"observed_any": False, "body": None}

    def _on_response(response) -> None:
        url = response.url
        if "self-api.baemin.com" not in url:
            return
        if urlparse(url).path != "/v4/cpc/bookings/by-shop-number":
            return
        state["observed_any"] = True
        if response.status == 200:
            try:
                state["body"] = response.json()
            except Exception:
                pass

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/ad/campaign")
        except Exception as e:
            raise BaeminAdsScrapeError(f"광고·서비스관리 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(3_000)
        _dismiss_backdrop_if_present(page)
        page.wait_for_timeout(1_000)
    finally:
        page.remove_listener("response", _on_response)

    if not state["observed_any"]:
        raise BaeminAdsScrapeError("CPC 입찰가 API 응답을 한 번도 확인하지 못했습니다")
    if state["body"] is None:
        raise BaeminAdsScrapeError("CPC 입찰가 API 응답을 받았지만 파싱하지 못했습니다")

    body = state["body"]
    try:
        return {
            "bid": int(body["bid"]),
            "max_bid": int(body["maxBid"]),
            "monthly_budget": int(body["monthlyBudget"]),
            "spent_budget": int(body["spentBudget"]),
            "is_auto_bidding": bool(body["isAutoBidding"]),
        }
    except (KeyError, TypeError) as e:
        raise BaeminAdsScrapeError(f"CPC 입찰가 응답 형태가 예상과 다릅니다: {e}") from e
```

이 파일 상단에 `from urllib.parse import urlparse`가 이미 import돼 있으므로 추가 import는 필요 없다.

- [ ] **Step 2: 회귀 테스트 확인**

이 함수는 Playwright가 필요해 pytest로 직접 덮지 않는다(Global Constraints, `fetch_shop_info`와 동일 컨벤션). 대신 기존 스위트가 깨지지 않았는지만 확인한다.

Run: `cd backend && .venv/bin/pytest tests/test_baemin_ads.py -v`
Expected: 기존 테스트 전부 PASS (새 함수 추가로 인한 import 에러 등이 없어야 함)

- [ ] **Step 3: 커밋**

```bash
git add backend/scrapers/baemin_ads.py
git commit -m "feat: 배민 CPC 입찰가(우리가게클릭) 실측 읽기 스크레이퍼 추가"
```

---

### Task 4: 데이터 동기화에 CPC 실측 연동

**Files:**
- Modify: `backend/app/review_sync.py`
- Test: `backend/tests/test_review_sync.py`

**Interfaces:**
- Consumes: Task 3의 `fetch_cpc_booking(page, shop_no) -> dict`.
- Produces: "데이터 동기화" 버튼을 누르면 `shop_no` 있는 `ad_campaigns.current_cpc`가 실측 `bid` 값으로 갱신됨. 이후 태스크(5, 7)는 `current_cpc`가 최신 실측값이라고 가정한다.

이 파일의 테스트는 `sync_reviews_for_job(job, conn, db)`를 진입점으로 호출하고, `sync_setup` fixture(`job, conn = sync_setup`)가 인증정보·job·기본 mock을 이미 구성해준다. 여러 브랜드가 필요한 테스트는 `_FakeMultiShopSession`(`shops = [(11111, "브랜드A"), (22222, "브랜드B")]`)을 그대로 쓴다 — 아래 신규 테스트는 이 패턴을 그대로 따른다(`test_sync_upserts_brand_click_metrics_per_shop` 등 기존 테스트와 동일 구조).

- [ ] **Step 1: `backend/app/review_sync.py` — import에 `fetch_cpc_booking` 추가**

파일 상단의 import 블록:

```python
from scrapers.baemin_ads import BaeminAdsScrapeError, fetch_brand_click_metrics, map_click_metrics_by_date
```

다음으로 교체:

```python
from scrapers.baemin_ads import BaeminAdsScrapeError, fetch_brand_click_metrics, fetch_cpc_booking, map_click_metrics_by_date
```

또한 이 파일은 이미 `from app.models import (...)`가 리뷰/정산 관련 모델만 import하고 있으므로, `AdCampaign`을 추가로 import해야 한다:

```python
from app.models import (
    BaeminShopBrand,
    BrandAdClickMetric,
    DailySettlement,
    Order,
    RepurchaseMetric,
    Review,
    ReviewReply,
    ReviewSyncJob,
    StorePlatformConnection,
)
```

다음으로 교체:

```python
from app.models import (
    AdCampaign,
    BaeminShopBrand,
    BrandAdClickMetric,
    DailySettlement,
    Order,
    RepurchaseMetric,
    Review,
    ReviewReply,
    ReviewSyncJob,
    StorePlatformConnection,
)
```

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_review_sync.py` 끝에 추가:

```python
def test_sync_updates_campaign_current_cpc_from_real_bid(db_session, sync_setup, monkeypatch):
    """브랜드별로 fetch_cpc_booking이 반환한 bid로 ad_campaigns.current_cpc가
    갱신돼야 한다 — 브랜드마다 다른 값을 줘서 취급이 뒤섞이지 않는지도 함께
    확인한다. shop_no 11111/22222는 _FakeMultiShopSession의 값과 맞춘다."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    campaign_a = AdCampaign(
        store_id=job.store_id, category="치킨", current_cpc=1, target_rank=3,
        status="active", shop_no="11111",
    )
    campaign_b = AdCampaign(
        store_id=job.store_id, category="찜·탕·찌개", current_cpc=1, target_rank=10,
        status="active", shop_no="22222",
    )
    db_session.add_all([campaign_a, campaign_b])
    db_session.commit()

    fake_session = _FakeMultiShopSession()
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])

    def fake_fetch_cpc_booking(page, shop_no):
        return {
            11111: {"bid": 95, "max_bid": 860, "monthly_budget": 1_000_000, "spent_budget": 150_065, "is_auto_bidding": False},
            22222: {"bid": 60, "max_bid": 500, "monthly_budget": 500_000, "spent_budget": 20_000, "is_auto_bidding": False},
        }[shop_no]

    monkeypatch.setattr(review_sync_mod, "fetch_cpc_booking", fake_fetch_cpc_booking)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    db_session.refresh(campaign_a)
    db_session.refresh(campaign_b)
    assert campaign_a.current_cpc == 95
    assert campaign_b.current_cpc == 60


def test_sync_isolates_cpc_booking_failure_from_click_metrics(db_session, sync_setup, monkeypatch):
    """CPC 입찰가 조회 실패가 같은 브랜드의 우리가게클릭 수집 성공까지
    막으면 안 된다 — 브랜드별 독립 실패 격리 원칙(리뷰/매출과 동일)."""
    import app.review_sync as review_sync_mod

    job, conn = sync_setup
    campaign = AdCampaign(
        store_id=job.store_id, category="치킨", current_cpc=1, target_rank=3,
        status="active", shop_no="99999001",
    )
    db_session.add(campaign)
    db_session.commit()

    fake_session = _FakeSession()  # shop_no=99999001
    monkeypatch.setattr(review_sync_mod, "baemin_login", lambda login_id, password: fake_session)
    monkeypatch.setattr(review_sync_mod, "fetch_all_reviews", lambda page, shop_no: [])
    monkeypatch.setattr(
        review_sync_mod, "fetch_brand_click_metrics",
        lambda page, shop_no, months: [_CLICK_RESP_AUGUST],
    )

    def _raise_cpc(page, shop_no):
        raise BaeminAdsScrapeError("CPC 입찰가 API 응답을 한 번도 확인하지 못했습니다")

    monkeypatch.setattr(review_sync_mod, "fetch_cpc_booking", _raise_cpc)

    sync_reviews_for_job(job, conn, db_session)

    assert job.status == "success"
    db_session.refresh(campaign)
    assert campaign.current_cpc == 1  # 실패했으니 갱신 안 됨
    row = db_session.query(BrandAdClickMetric).filter_by(
        store_id=job.store_id, platform_id=job.platform_id, shop_no="99999001", metric_date="2026-08-01",
    ).one()
    assert row.ad_spend == 95  # 클릭 성과는 CPC 실패와 무관하게 정상 수집됨
```

`_CLICK_RESP_AUGUST`는 이 파일 1110번째 줄 근처에 이미 정의돼 있다(`dailyMetrics`에 `spentBudget: 95`인 2026-08-01 항목 포함 — `test_sync_upserts_brand_click_metrics_per_shop`가 참조하는 것과 같은 상수).

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k current_cpc`
Expected: `test_sync_updates_campaign_current_cpc_from_real_bid`만 FAIL(아직 동기화 루프가 `fetch_cpc_booking`을 호출하지 않으므로 `current_cpc`가 여전히 1). `test_sync_isolates_cpc_booking_failure_from_click_metrics`는 이 시점에도 우연히 PASS할 수 있다(애초에 아무것도 안 건드리므로 `current_cpc == 1`이 참) — 회귀 방지용 테스트라 지금 실패할 필요는 없다, 다음 Step 이후에도 계속 PASS인지만 확인하면 된다.

- [ ] **Step 4: `backend/app/review_sync.py` — 우리가게클릭 동기화 루프에 CPC 갱신 추가**

"우리가게클릭은 매출/입금/재주문율과 달리..." 주석으로 시작하는 기존 루프:

```python
        for shop_no, shop_name in session.shops:
            try:
                click_responses = fetch_brand_click_metrics(session.page, shop_no, months)
                click_by_date = map_click_metrics_by_date(click_responses)
                for metric_date, m in click_by_date.items():
                    upsert_brand_ad_click_metric(
                        db, job.store_id, job.platform_id, str(shop_no), metric_date,
                        ad_spend=m["ad_spend"], impressions=m["impressions"], clicks=m["clicks"],
                        ad_orders=m["ad_orders"], ad_revenue=m["ad_revenue"],
                    )
                if click_by_date:
                    stats_succeeded_any = True
            except Exception as e:
                stats_errors.append(f"{shop_name} 우리가게클릭 동기화 실패: {e}")
```

다음으로 교체(같은 루프 안에서 CPC 조회를 이어붙인다 — 별도 루프로 분리하면 매장마다 로그인된 같은 `session.page`를 다시 순회해야 해서 비효율이고, 클릭 성과 실패와 CPC 실패를 독립적으로 기록해야 하므로 `try` 블록은 나눈다):

```python
        for shop_no, shop_name in session.shops:
            try:
                click_responses = fetch_brand_click_metrics(session.page, shop_no, months)
                click_by_date = map_click_metrics_by_date(click_responses)
                for metric_date, m in click_by_date.items():
                    upsert_brand_ad_click_metric(
                        db, job.store_id, job.platform_id, str(shop_no), metric_date,
                        ad_spend=m["ad_spend"], impressions=m["impressions"], clicks=m["clicks"],
                        ad_orders=m["ad_orders"], ad_revenue=m["ad_revenue"],
                    )
                if click_by_date:
                    stats_succeeded_any = True
            except Exception as e:
                stats_errors.append(f"{shop_name} 우리가게클릭 동기화 실패: {e}")

            # CPC 입찰가는 캠페인이 실제로 이 shop_no에 연결돼 있을 때만 갱신한다
            # (ad_campaigns에 아직 이 브랜드의 캠페인이 없으면 조용히 건너뛴다 —
            # Task 2로 4브랜드 캠페인이 이미 있는 게 보통이지만, 신규 브랜드가
            # 연결 직후(캠페인 미생성) 동기화되는 경우까지 방어한다).
            campaign = db.scalar(select(AdCampaign).where(AdCampaign.shop_no == str(shop_no)))
            if campaign is not None:
                try:
                    booking = fetch_cpc_booking(session.page, shop_no)
                    campaign.current_cpc = booking["bid"]
                    stats_succeeded_any = True
                except Exception as e:
                    stats_errors.append(f"{shop_name} CPC 입찰가 동기화 실패: {e}")
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_review_sync.py -v -k "current_cpc or isolates_cpc_booking"`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 6: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/review_sync.py backend/tests/test_review_sync.py
git commit -m "feat: 데이터 동기화 시 배민 실제 CPC 입찰가로 ad_campaigns.current_cpc 갱신"
```

---

### Task 5: `GET /ads/rank-monitoring` 실데이터 추천 로직 정리 + 목표순위 수정 엔드포인트

**Files:**
- Modify: `backend/app/routers/ads.py`
- Test: `backend/tests/test_ads.py`

**Interfaces:**
- Consumes: 없음(기존 `AdCampaign.current_cpc`, `AdRankSnapshot` 그대로 사용).
- Produces: `PATCH /ads/campaigns/{campaign_id}` — body `{"target_rank": int}` → `{"campaign_id": int, "target_rank": int}`. Task 7이 이 엔드포인트를 호출한다. `GET /ads/rank-monitoring`의 `shop_no` 있는 캠페인 응답에서 `competitor_est_cpc`는 항상 `null`, `suggested_cpc`는 순위 미달 시 `current_cpc + 30`.

- [ ] **Step 1: `backend/app/routers/ads.py` 상단에 `_BID_STEP_WON` 상수 + `pydantic` import 추가**

파일 상단의 import 블록:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
```

다음으로 교체:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
```

`_CRAWL_TIMEOUT_SEC = 900` 줄 다음에 상수 추가:

```python
_BID_STEP_WON = 30  # 순위 미달 시 다음 시도 추천 증액폭(설계 문서 — 사용자 제안 10~50원 중 기본값)
```

- [ ] **Step 2: 실패하는 테스트 작성 — 추천 로직 변경**

`backend/tests/test_ads.py`의 `test_rank_monitoring_uses_real_distance_snapshot_when_shop_no_set`:

```python
    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] == 36  # 가장 최신인 Mock(1위)도, 가장 최신인 1.5~2.5km(9위)도 아니라 distance_km=0(36위)
    assert row["rank_status"] == "rank_dropped"  # 36 > target_rank(3)
    assert row["recommended_action"] == "raise_cpc"
    assert row["suggested_cpc"] is None  # 경쟁 CPC를 몰라 구체적 액수는 못 줌
```

다음으로 교체:

```python
    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] == 36  # 가장 최신인 Mock(1위)도, 가장 최신인 1.5~2.5km(9위)도 아니라 distance_km=0(36위)
    assert row["rank_status"] == "rank_dropped"  # 36 > target_rank(3)
    assert row["recommended_action"] == "raise_cpc"
    assert row["competitor_est_cpc"] is None  # 배민이 노출하지 않아 항상 None(추정치 계산 제거)
    assert row["suggested_cpc"] == campaign.current_cpc + 30  # 실제 현재 CPC 기준 +30원 추천(campaign 기본 current_cpc=400)
```

파일 끝에 신규 테스트 추가:

```python
def test_update_campaign_target_rank(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=5, shop_no="14804318")

    res = client.patch(f"/ads/campaigns/{campaign.id}", json={"target_rank": 2}, headers=auth_headers)

    assert res.status_code == 200
    assert res.json() == {"campaign_id": campaign.id, "target_rank": 2}
    db_session.refresh(campaign)
    assert campaign.target_rank == 2


def test_update_campaign_target_rank_rejects_other_users_campaign(client, db_session, seeded_user, auth_headers):
    other_campaign = make_campaign(db_session, seeded_user["store"], target_rank=5, shop_no="14804318")
    # _campaign_for_user는 store.user_id로 소유권을 확인한다 — 다른 유저 소유
    # 캠페인이면 404여야 한다. 여기서는 store_id를 존재하지 않는 값으로 바꿔
    # 같은 효과(소유권 불일치)를 낸다.
    other_campaign.store_id = other_campaign.store_id + 99999
    db_session.commit()

    res = client.patch(f"/ads/campaigns/{other_campaign.id}", json={"target_rank": 2}, headers=auth_headers)

    assert res.status_code == 404


def test_update_campaign_target_rank_rejects_below_one(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=5, shop_no="14804318")

    res = client.patch(f"/ads/campaigns/{campaign.id}", json={"target_rank": 0}, headers=auth_headers)

    assert res.status_code == 422
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k "target_rank or rank_monitoring_uses_real"`
Expected: FAIL — `suggested_cpc` 단언 실패 + `PATCH /ads/campaigns/{id}` 404(라우트 없음)

- [ ] **Step 4: `backend/app/routers/ads.py` — `GET /ads/rank-monitoring` 추천 로직 교체**

```python
            current_rank = real_latest.current_rank if real_latest else None
            rank_status = ("rank_dropped" if current_rank > c.target_rank else "normal") if current_rank is not None else None
            recommended_action = "raise_cpc" if rank_status == "rank_dropped" else "keep"
            competitor_est_cpc = round(c.current_cpc * 1.15) if current_rank is not None and rank_status == "rank_dropped" else None
            result.append({
                "campaign_id": c.id,
                "category": c.category,
                "current_cpc": c.current_cpc,
                "target_rank": c.target_rank,
                "status": c.status,
                "current_rank": current_rank,
                "competitor_est_cpc": competitor_est_cpc,
                "rank_status": rank_status,
                "recommended_action": recommended_action,
                "suggested_cpc": None,  # 경쟁 CPC를 실측할 방법이 없어 구체적 액수는 안 줌
                "snapshot_at": real_latest.snapshot_at.isoformat() if real_latest else None,
            })
            continue
```

다음으로 교체:

```python
            current_rank = real_latest.current_rank if real_latest else None
            rank_status = ("rank_dropped" if current_rank > c.target_rank else "normal") if current_rank is not None else None
            recommended_action = "raise_cpc" if rank_status == "rank_dropped" else "keep"
            suggested_cpc = c.current_cpc + _BID_STEP_WON if rank_status == "rank_dropped" else None
            result.append({
                "campaign_id": c.id,
                "category": c.category,
                "current_cpc": c.current_cpc,
                "target_rank": c.target_rank,
                "status": c.status,
                "current_rank": current_rank,
                "competitor_est_cpc": None,  # 배민이 노출하지 않아 실측 불가 — 항상 None(프론트가 컬럼 자체를 제거)
                "rank_status": rank_status,
                "recommended_action": recommended_action,
                "suggested_cpc": suggested_cpc,
                "snapshot_at": real_latest.snapshot_at.isoformat() if real_latest else None,
            })
            continue
```

- [ ] **Step 5: `backend/app/routers/ads.py` — `PATCH /ads/campaigns/{campaign_id}` 엔드포인트 추가**

`_campaign_for_user` 함수 정의 바로 다음, `@router.post("/ads/rank-by-distance/run")` 앞에 삽입:

```python
class UpdateCampaignRequest(BaseModel):
    target_rank: int = Field(ge=1)


@router.patch("/ads/campaigns/{campaign_id}")
def ads_update_campaign(
    campaign_id: int,
    body: UpdateCampaignRequest,
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    campaign = _campaign_for_user(campaign_id, user, db)
    campaign.target_rank = body.target_rank
    db.commit()
    return {"campaign_id": campaign.id, "target_rank": campaign.target_rank}
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k "target_rank or rank_monitoring_uses_real"`
Expected: 전부 PASS

- [ ] **Step 7: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 8: 커밋**

```bash
git add backend/app/routers/ads.py backend/tests/test_ads.py
git commit -m "feat: 실데이터 캠페인 CPC 추천을 실측 기반(+30원)으로 교체, 목표순위 수정 엔드포인트 추가"
```

---

### Task 6: 쓰기 스크레이퍼 라이브 캡처 + `submit_cpc_bid` + `POST /ads/rank-by-distance/apply-bid`

> ⚠️ **이 태스크의 Step 1은 subagent에게 위임하지 않는다.** orchestrator가 직접 수행한다. 사용자의 실제 배민 광고 계정에 진짜 금액을 반영하는 이 프로젝트 최초의 쓰기 액션이다 — 진행 직전 사용자에게 채팅으로 "지금부터 배민 실 계정에 입찰가를 실제로 반영하는 요청을 한 번 캡처하려고 합니다. 시험 금액은 [현재 CPC + 10원 정도의 작은 값]으로 하려는데 괜찮을까요?" 형태로 실시간 동의를 구하고, 동의 후에만 진행한다. Step 2부터는 일반 implementer subagent로 위임 가능하다.

**Files:**
- Modify: `backend/scrapers/baemin_ads.py` (`submit_cpc_bid` 함수 — Step 1 결과에 따라 구현)
- Modify: `backend/app/routers/ads.py` (`POST /ads/rank-by-distance/apply-bid` 엔드포인트)
- Test: `backend/tests/test_ads.py`

**Interfaces:**
- Consumes: Task 5의 `_campaign_for_user`, 기존 `_start_crawl_job`/`_execute_crawl_job`/`_crawl_lock`/`_job_state` 인프라, `_run_local_crawl(campaign_id) -> tuple[int, int]`.
- Produces: `submit_cpc_bid(page, shop_no: str, amount: int) -> None`(성공 시 반환값 없음, 실패 시 `BaeminAdsScrapeError`). `POST /ads/rank-by-distance/apply-bid?campaign_id=&amount=` → `{"status": "started"}`(기존 `POST /ads/rank-by-distance/run`과 동일 계약 — Task 7은 기존 `GET /ads/rank-by-distance/run/status` 폴링을 그대로 재사용한다).

- [ ] **Step 1(orchestrator 전용): 실 계정에서 "적용" 버튼 요청 캡처**

사용자 동의를 받은 뒤:
1. `backend/scrapers/baemin_auth.py`의 `login(login_id, password)`로 실 계정에 로그인한다(자격증명은 `store_platform_connections.credential_ciphertext`를 `decrypt_credential`로 복호화 — 이미 이 세션에서 여러 번 쓴 패턴).
2. `https://self.baemin.com/shops/14804318/ad/campaign`으로 이동, "우리가게클릭" 화살표 펼치기 → "광고·서비스" → "수정" 클릭 → "광고 금액 수정" 모달에서 "수동 설정 모드" 선택 상태 유지 → "클릭당 희망 광고금액" 필드를 사용자가 승인한 시험 금액으로 변경.
3. `page.on("request", ...)`로 요청을 가로챈 상태에서 "적용" 버튼을 클릭하고, 실제로 발생한 HTTP 요청의 메서드/URL/헤더/바디를 기록한다.
4. 응답이 성공(2xx)인지, 실패 시 어떤 상태 코드/바디를 주는지도 함께 기록한다.
5. 기록한 내용(엔드포인트, 메서드, 요청 바디 스키마, 성공/실패 응답 형태)을 아래 Step 2의 스타팅 코드에 반영한다 — 이 문서의 스타팅 코드는 실제 캡처 결과로 **교체**해야 한다(아래 코드는 관찰된 UI 흐름 기준의 최선 추정이며 필드명 등은 실제 요청과 다를 수 있다).

- [ ] **Step 2: `backend/scrapers/baemin_ads.py` — `submit_cpc_bid` 구현**

Step 1에서 캡처한 실제 요청 형태로 아래 스타팅 코드를 교체해 작성한다(가장 가능성 높은 추정 — PUT/PATCH로 CPC 입찰 설정을 갱신하는 형태):

```python
def submit_cpc_bid(page, shop_no: str, amount: int) -> None:
    """"광고 금액 수정" 모달에서 "클릭당 희망 광고금액"을 amount(원)로 실제
    반영한다("수동 설정 모드" 유지, "스마트 모드"로 전환하지 않는다). 배민
    계정에 진짜 쓰기가 발생하는 함수 — 호출 전 사용자의 명시적 동의가
    이미 끝났다는 전제로 만들어졌다(POST /ads/rank-by-distance/apply-bid만
    호출한다, 다른 어떤 경로도 이 함수를 호출하지 않는다).

    [Step 1 라이브 캡처로 확인한 실제 엔드포인트/페이로드로 아래 구현을
    교체할 것 — 이 스타팅 코드는 관찰된 UI 흐름 기반 추정이다.]
    """
    state = {"status": None}

    def _on_response(response) -> None:
        if "self-api.baemin.com" not in response.url:
            return
        if urlparse(response.url).path != "/v4/cpc/bookings/by-shop-number":
            return
        if response.request.method not in ("PUT", "PATCH", "POST"):
            return
        state["status"] = response.status

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/ad/campaign")
        except Exception as e:
            raise BaeminAdsScrapeError(f"광고·서비스관리 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(3_000)
        _dismiss_backdrop_if_present(page)

        page.get_by_text("광고·서비스").first.click(timeout=5_000)
        page.wait_for_timeout(500)
        page.get_by_text("수정", exact=True).first.click(timeout=5_000)
        page.wait_for_timeout(1_000)

        bid_input = page.get_by_label("클릭당 희망 광고금액")
        bid_input.fill(str(amount))
        page.wait_for_timeout(300)

        page.get_by_role("button", name="적용").first.click(timeout=5_000)
        page.wait_for_timeout(2_000)
    finally:
        page.remove_listener("response", _on_response)

    if state["status"] is None:
        raise BaeminAdsScrapeError("입찰가 반영 요청 응답을 확인하지 못했습니다")
    if state["status"] != 200:
        raise BaeminAdsScrapeError(f"입찰가 반영 요청이 실패했습니다 (status={state['status']})")
```

- [ ] **Step 3: 실패하는 테스트 작성 — `apply-bid` 엔드포인트**

`backend/tests/test_ads.py` 끝에 추가:

```python
def test_apply_bid_updates_current_cpc_and_starts_crawl(
    db_session, seeded_user, platforms, monkeypatch, tmp_path
):
    """입찰 제출 성공 → current_cpc 갱신 → 기존 크롤 인프라(_run_local_crawl)로
    이어지는 흐름을 검증한다. submit_cpc_bid/baemin_login/subprocess.run을
    전부 mock해 실제 Playwright/배민 접근 없이 로직만 확인한다."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    campaign = make_campaign(db_session, seeded_user["store"], current_cpc=95, shop_no="14804318")
    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    monkeypatch.setattr(ads_module, "baemin_login", lambda login_id, password: Mock(page=Mock(), close=Mock()))
    monkeypatch.setattr(ads_module, "submit_cpc_bid", lambda page, shop_no, amount: None)
    monkeypatch.setattr(ads_module, "time", Mock(sleep=Mock()))
    # _apply_bid_then_crawl은 성공하면 이어서 _run_local_crawl을 호출하고,
    # shop_no가 있는 캠페인이라 그 안에서 다시 로그인 + fetch_shop_info를
    # 부른다(입찰가 제출용 로그인과는 별개 호출) — 이것도 mock해야
    # _run_local_crawl이 실제 배민 응답을 기다리다 502로 죽지 않는다.
    monkeypatch.setattr(ads_module, "fetch_shop_info", lambda page, shop_no: {
        "name": "치밥대장", "category": "치킨", "road_address": "서울시 노원구",
        "latitude": 37.6, "longitude": 127.0,
    })
    fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ads_module.subprocess, "run", Mock(return_value=fake_proc))
    monkeypatch.setattr(ads_module, "ingest_csv", lambda csv_path, campaign_id: (1, 0))

    inserted, skipped = ads_module._apply_bid_then_crawl(campaign.id, 125)

    assert (inserted, skipped) == (1, 0)
    db_session.refresh(campaign)
    assert campaign.current_cpc == 125
    ads_module.time.sleep.assert_called_once_with(ads_module._BID_APPLY_WAIT_SEC)


def test_apply_bid_rejects_campaign_without_shop_no(db_session, seeded_user, monkeypatch):
    campaign = make_campaign(db_session, seeded_user["store"], shop_no=None)
    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        ads_module._apply_bid_then_crawl(campaign.id, 125)

    assert exc_info.value.status_code == 500


def test_apply_bid_endpoint_blocked_for_basic_plan(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    res = client.post(
        f"/ads/rank-by-distance/apply-bid?campaign_id={campaign.id}&amount=125", headers=auth_headers
    )
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "pro_required"


def test_apply_bid_endpoint_rejects_non_positive_amount(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    res = client.post(
        f"/ads/rank-by-distance/apply-bid?campaign_id={campaign.id}&amount=0", headers=auth_headers
    )
    assert res.status_code == 400
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k apply_bid`
Expected: FAIL — `AttributeError: module 'app.routers.ads' has no attribute '_apply_bid_then_crawl'`

- [ ] **Step 5: `backend/app/routers/ads.py` — `apply-bid` 인프라 + 엔드포인트 추가**

파일 상단 import에 `time` 추가:

```python
import hmac
import os
import pathlib
import subprocess
import threading
from datetime import date, timedelta
```

다음으로 교체:

```python
import hmac
import os
import pathlib
import subprocess
import threading
import time
from datetime import date, timedelta
```

`from scrapers.baemin_ads import ...`가 없으므로(현재 이 파일은 `baemin_ads`를 import하지 않는다) 다음 import를 추가한다. `from scrapers.baemin_auth import login as baemin_login` 줄 앞에(이 파일의 기존 import는 알파벳 순 — `baemin_ads` < `baemin_auth`):

```python
from scrapers.baemin_ads import submit_cpc_bid
from scrapers.baemin_auth import login as baemin_login
```

`_CRAWL_TIMEOUT_SEC`/`_BID_STEP_WON` 상수 근처에 대기 시간 상수 추가:

```python
_BID_APPLY_WAIT_SEC = 30  # 배민 쪽 반영 시차 대기(설계 문서 — 화면 안내문구 확인, 구현 시 조정 가능)
```

`_run_local_crawl` 함수 바로 다음, `_execute_crawl_job` 앞에 새 함수 추가:

```python
def _apply_bid_then_crawl(campaign_id: int, amount: int) -> tuple[int, int]:
    """입찰가를 배민에 실제로 반영한 뒤(쓰기, submit_cpc_bid), 배민 쪽 반영
    시차를 기다렸다가(_BID_APPLY_WAIT_SEC) 기존 _run_local_crawl로 순위를
    재측정한다. 반드시 백그라운드 스레드에서만 호출한다(로그인+제출+대기+
    크롤이 합쳐 수십 초~수 분 걸리는 블로킹 호출)."""
    db = SessionLocal()
    try:
        campaign = db.get(AdCampaign, campaign_id)
        if campaign is None or not campaign.shop_no:
            raise HTTPException(500, f"캠페인 {campaign_id}은 실데이터 캠페인이 아니라 입찰가를 반영할 수 없습니다")
        baemin_platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
        conn = db.scalar(
            select(StorePlatformConnection).where(
                StorePlatformConnection.store_id == campaign.store_id,
                StorePlatformConnection.platform_id == baemin_platform.id,
            )
        ) if baemin_platform else None
        if conn is None:
            raise HTTPException(500, f"캠페인 {campaign_id}의 배민 연결을 찾을 수 없습니다")
        try:
            credential = decrypt_credential(conn.credential_ciphertext)
            session = baemin_login(credential["login_id"], credential["password"])
            try:
                submit_cpc_bid(session.page, campaign.shop_no, amount)
            finally:
                session.close()
        except Exception as e:
            raise HTTPException(502, f"입찰가 반영에 실패했습니다: {e}") from e

        campaign.current_cpc = amount
        db.commit()
    finally:
        db.close()

    time.sleep(_BID_APPLY_WAIT_SEC)
    return _run_local_crawl(campaign_id)


def _execute_bid_apply_job(campaign_id: int, amount: int) -> None:
    """백그라운드 스레드에서 실행된다. _crawl_lock은 호출부(apply-bid 엔드포인트)가
    이미 잡아뒀고, 끝나면 이 함수가 반드시 놓아준다(_execute_crawl_job과 동일 계약)."""
    try:
        inserted, skipped = _apply_bid_then_crawl(campaign_id, amount)
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="done", inserted=inserted, skipped=skipped, error=None)
    except HTTPException as e:
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="error", inserted=None, skipped=None, error=e.detail)
    except Exception as e:  # noqa: BLE001
        with _job_state_lock:
            _job_state.update(campaign_id=campaign_id, status="error", inserted=None, skipped=None, error=str(e))
    finally:
        _crawl_lock.release()
```

`_campaign_for_user` 함수 다음, `PATCH /ads/campaigns/{campaign_id}` 다음(Task 5에서 추가한 위치 바로 아래)에 엔드포인트 추가:

```python
@router.post("/ads/rank-by-distance/apply-bid")
def ads_apply_bid(
    campaign_id: int,
    amount: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """사용자가 목표 금액을 직접 확인·클릭했을 때만 호출된다(프론트엔드가
    "OO원으로 배민에 실제 반영됩니다" 확인 다이얼로그를 거친다). 성공하면
    기존 POST /ads/rank-by-distance/run과 동일한 {"status": "started"} 계약으로
    응답하고, 진행 상황/결과는 기존 GET /ads/rank-by-distance/run/status를
    그대로 폴링해서 확인한다(새 상태 엔드포인트를 만들지 않는다)."""
    campaign = _campaign_for_user(campaign_id, user, db)
    if not campaign.shop_no:
        raise HTTPException(400, "실데이터 캠페인만 입찰가를 반영할 수 있습니다")
    if amount < 1:
        raise HTTPException(400, "입찰 금액은 1원 이상이어야 합니다")
    if not _crawl_lock.acquire(blocking=False):
        raise HTTPException(409, "이미 다른 순위 확인이 진행 중입니다. 잠시 후 다시 시도하세요.")
    with _job_state_lock:
        _job_state.update(campaign_id=campaign_id, status="running", inserted=None, skipped=None, error=None)
    background_tasks.add_task(_execute_bid_apply_job, campaign_id, amount)
    return {"status": "started"}
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k apply_bid`
Expected: 전부 PASS

- [ ] **Step 7: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 8: 커밋**

```bash
git add backend/scrapers/baemin_ads.py backend/app/routers/ads.py backend/tests/test_ads.py
git commit -m "feat: 사용자 승인 기반 CPC 입찰가 실반영 + 자동 재측정 엔드포인트 추가"
```

---

### Task 7: 프론트엔드 — Mock/경쟁CPC 문구 제거 + 브랜드별 입찰 조정 UI

**Files:**
- Modify: `frontend/src/app/(app)/ads/page.tsx`

**Interfaces:**
- Consumes: Task 5의 `PATCH /ads/campaigns/{campaign_id}`, Task 6의 `POST /ads/rank-by-distance/apply-bid`(응답 계약이 기존 `POST /ads/rank-by-distance/run`과 동일하므로 기존 폴링 로직을 재사용).
- Produces: 없음(최종 화면).

- [ ] **Step 1: 안내 배너 문구 교체**

```tsx
      <div>
        <h1 className="text-xl font-semibold">광고 순위 모니터링</h1>
        <p className="text-sm text-muted">
          치밥대장은 실제 배민 데이터 기반입니다 — 현재 순위는 아래 반경별 실측(실기기
          자동화) 중 가게 주소 지점(0km) 결과, 광고 성과는 우리가게클릭 실데이터입니다.
          경쟁 가게 CPC만은 배민이 노출하지 않아 추정치입니다. 나머지 캠페인은 수집됐다고
          가정한 Mock 스냅샷입니다. CPC 자동 입찰은 하지 않습니다.
        </p>
      </div>
```

다음으로 교체:

```tsx
      <div>
        <h1 className="text-xl font-semibold">광고 순위 모니터링</h1>
        <p className="text-sm text-muted">
          4개 브랜드(치밥대장/곱도리탕/블랙닭갈비/행복가성비) 모두 실제 배민 데이터
          기반입니다 — 현재 CPC는 배민 우리가게클릭 실제 입찰가, 순위는 아래 반경별
          실측(실기기 자동화) 중 가게 주소 지점(0km) 결과, 광고 성과는 우리가게클릭
          실데이터입니다. 경쟁 가게의 CPC는 배민이 노출하지 않아 알 수 없습니다. CPC
          자동 입찰은 하지 않으며, 배민에 실제로 반영되는 건 아래에서 직접
          &quot;적용하기&quot;를 눌렀을 때뿐입니다.
        </p>
      </div>
```

- [ ] **Step 2: `RankRow` 타입에서 `competitor_est_cpc` 제거, "경쟁 예상 CPC" 컬럼 제거**

```tsx
type RankRow = {
  campaign_id: number;
  category: string;
  current_cpc: number;
  target_rank: number;
  status: "active" | "paused";
  current_rank: number | null;
  competitor_est_cpc: number | null;
  rank_status: "normal" | "rank_dropped" | null;
  recommended_action: "keep" | "raise_cpc" | "lower_cpc";
  suggested_cpc: number | null;
  snapshot_at: string | null;
};
```

다음으로 교체:

```tsx
type RankRow = {
  campaign_id: number;
  category: string;
  current_cpc: number;
  target_rank: number;
  status: "active" | "paused";
  current_rank: number | null;
  rank_status: "normal" | "rank_dropped" | null;
  recommended_action: "keep" | "raise_cpc" | "lower_cpc";
  suggested_cpc: number | null;
  snapshot_at: string | null;
};
```

"순위 현황" 테이블 헤더:

```tsx
                <th className="py-2 font-medium">카테고리</th>
                <th className="font-medium">현재 CPC</th>
                <th className="font-medium">목표 순위</th>
                <th className="font-medium">현재 순위</th>
                <th className="font-medium">경쟁 예상 CPC (추정)</th>
                <th className="font-medium">상태</th>
                <th className="font-medium">추천 액션</th>
```

다음으로 교체:

```tsx
                <th className="py-2 font-medium">카테고리</th>
                <th className="font-medium">현재 CPC</th>
                <th className="font-medium">목표 순위</th>
                <th className="font-medium">현재 순위</th>
                <th className="font-medium">상태</th>
                <th className="font-medium">추천 액션</th>
```

테이블 바디의 해당 셀과 `colSpan`:

```tsx
                    <td className={`font-semibold ${dropped ? "text-danger" : "text-success"}`}>
                      {r.current_rank === null ? "—" : `${r.current_rank}위`}
                    </td>
                    <td>{r.competitor_est_cpc === null ? "—" : won(r.competitor_est_cpc)}</td>
                    <td>
```

다음으로 교체:

```tsx
                    <td className={`font-semibold ${dropped ? "text-danger" : "text-success"}`}>
                      {r.current_rank === null ? "—" : `${r.current_rank}위`}
                    </td>
                    <td>
```

그리고:

```tsx
                  <td colSpan={7} className="py-6 text-center text-sm text-muted">등록된 광고 캠페인이 없습니다.</td>
```

다음으로 교체:

```tsx
                  <td colSpan={6} className="py-6 text-center text-sm text-muted">등록된 광고 캠페인이 없습니다.</td>
```

- [ ] **Step 3: 크롤 폴링 로직을 `handleRunCheck`/`handleApplyBid` 공용 헬퍼로 분리**

```tsx
  async function handleRunCheck(campaignId: number) {
    setRunningCampaignId(campaignId);
    setRunError(null);
    try {
      await apiPost<{ status: string }>(`/ads/rank-by-distance/run?campaign_id=${campaignId}`);

      while (true) {
        await sleep(5000);
        const status = await apiGet<{
          status: "idle" | "running" | "done" | "error";
          inserted?: number;
          skipped?: number;
          points?: DistancePoint[];
          error?: string;
        }>(`/ads/rank-by-distance/run/status?campaign_id=${campaignId}`);

        if (status.status === "done") {
          setDistanceRanks((prev) =>
            prev.map((c) => (c.campaign_id === campaignId ? { ...c, points: status.points ?? c.points } : c))
          );
          break;
        }
        if (status.status === "error") {
          setRunError(status.error ?? "순위 확인 중 오류가 발생했습니다.");
          break;
        }
        // "running" 또는 "idle"(막 시작해서 아직 상태가 안 잡힌 순간)이면 계속 폴링
      }
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : "순위 확인 중 오류가 발생했습니다.");
    } finally {
      setRunningCampaignId(null);
    }
  }
```

다음으로 교체(폴링 while문을 `waitForCrawlResult`로 분리하고, `handleApplyBid`를 추가):

```tsx
  type CrawlStatus = {
    status: "idle" | "running" | "done" | "error";
    inserted?: number;
    skipped?: number;
    points?: DistancePoint[];
    error?: string;
  };

  async function waitForCrawlResult(campaignId: number): Promise<CrawlStatus> {
    while (true) {
      await sleep(5000);
      const status = await apiGet<CrawlStatus>(`/ads/rank-by-distance/run/status?campaign_id=${campaignId}`);

      if (status.status === "done") {
        setDistanceRanks((prev) =>
          prev.map((c) => (c.campaign_id === campaignId ? { ...c, points: status.points ?? c.points } : c))
        );
        return status;
      }
      if (status.status === "error") {
        setRunError(status.error ?? "순위 확인 중 오류가 발생했습니다.");
        return status;
      }
      // "running" 또는 "idle"(막 시작해서 아직 상태가 안 잡힌 순간)이면 계속 폴링
    }
  }

  async function handleRunCheck(campaignId: number) {
    setRunningCampaignId(campaignId);
    setRunError(null);
    try {
      await apiPost<{ status: string }>(`/ads/rank-by-distance/run?campaign_id=${campaignId}`);
      await waitForCrawlResult(campaignId);
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : "순위 확인 중 오류가 발생했습니다.");
    } finally {
      setRunningCampaignId(null);
    }
  }

  async function handleApplyBid(campaignId: number, amount: number) {
    if (!window.confirm(`${won(amount)}으로 배민에 실제 반영됩니다. 계속할까요?`)) return;
    setRunningCampaignId(campaignId);
    setRunError(null);
    try {
      await apiPost<{ status: string }>(
        `/ads/rank-by-distance/apply-bid?campaign_id=${campaignId}&amount=${amount}`
      );
      const result = await waitForCrawlResult(campaignId);
      if (result.status === "done") {
        apiGet<RankRow[]>(`/ads/rank-monitoring?store_id=${storeId}`).then(setRanks).catch(() => {});
      }
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : "입찰가 반영 중 오류가 발생했습니다.");
    } finally {
      setRunningCampaignId(null);
    }
  }

  async function handleUpdateTargetRank(campaignId: number, targetRank: number) {
    try {
      await apiPatch<{ campaign_id: number; target_rank: number }>(
        `/ads/campaigns/${campaignId}`, { target_rank: targetRank }
      );
      setRanks((prev) => prev.map((r) => (r.campaign_id === campaignId ? { ...r, target_rank: targetRank } : r)));
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : "목표 순위 저장 중 오류가 발생했습니다.");
    }
  }
```

`frontend/src/lib/api.ts`에 이미 `apiPatch<T>(path: string, body?: unknown): Promise<T>`가 있으므로(PATCH 고정, `request()` 공용 헬퍼 재사용) 새로 만들 필요 없다. 파일 상단 import만 갱신한다:

```tsx
import { Card } from "@/components/Card";
import { ApiError, apiGet, apiPost, won } from "@/lib/api";
```

다음으로 교체:

```tsx
import { Card } from "@/components/Card";
import { ApiError, apiGet, apiPatch, apiPost, won } from "@/lib/api";
```

- [ ] **Step 4: 입력 상태 추가**

`const [runError, setRunError] = useState<string | null>(null);` 다음에 추가:

```tsx
  const [bidInputs, setBidInputs] = useState<Record<number, string>>({});
  const [targetRankInputs, setTargetRankInputs] = useState<Record<number, string>>({});
```

- [ ] **Step 5: "반경별 실측 순위" 카드에 브랜드별 목표순위+입찰금액 입력 + 적용하기 추가**

```tsx
          {distanceRanks.map((c) => (
            <div key={c.campaign_id}>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-sm font-medium">{c.category}</p>
                <button
                  onClick={() => handleRunCheck(c.campaign_id)}
                  disabled={!LIVE_CRAWL_ENABLED || runningCampaignId !== null}
                  title={LIVE_CRAWL_ENABLED ? undefined : "이 환경에서는 실측 크롤링을 실행할 수 없습니다"}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {!LIVE_CRAWL_ENABLED
                    ? "우리가게 순위 확인 (사용 불가)"
                    : runningCampaignId === c.campaign_id
                      ? "순위 확인 중… (수 분 소요)"
                      : "우리가게 순위 확인"}
                </button>
              </div>
```

다음으로 교체(입찰 조정 인라인 폼 추가 — 대응하는 `RankRow`를 `ranks`에서 찾아 현재 CPC/목표순위 기본값으로 쓴다):

```tsx
          {distanceRanks.map((c) => {
            const rank = ranks.find((r) => r.campaign_id === c.campaign_id);
            const bidValue = bidInputs[c.campaign_id] ?? (rank ? String(rank.current_cpc) : "");
            const targetRankValue = targetRankInputs[c.campaign_id] ?? String(c.target_rank);
            return (
            <div key={c.campaign_id}>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium">{c.category}</p>
                <button
                  onClick={() => handleRunCheck(c.campaign_id)}
                  disabled={!LIVE_CRAWL_ENABLED || runningCampaignId !== null}
                  title={LIVE_CRAWL_ENABLED ? undefined : "이 환경에서는 실측 크롤링을 실행할 수 없습니다"}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {!LIVE_CRAWL_ENABLED
                    ? "우리가게 순위 확인 (사용 불가)"
                    : runningCampaignId === c.campaign_id
                      ? "순위 확인 중… (수 분 소요)"
                      : "우리가게 순위 확인"}
                </button>
              </div>
              <div className="mb-3 flex flex-wrap items-end gap-3 rounded-lg bg-surface-2 p-3">
                <label className="text-xs text-muted">
                  목표 순위
                  <input
                    type="number"
                    min={1}
                    value={targetRankValue}
                    onChange={(e) => setTargetRankInputs((prev) => ({ ...prev, [c.campaign_id]: e.target.value }))}
                    onBlur={() => {
                      const n = Number(targetRankValue);
                      if (Number.isInteger(n) && n >= 1) handleUpdateTargetRank(c.campaign_id, n);
                    }}
                    className="mt-1 block w-20 rounded border border-border-subtle bg-surface px-2 py-1 text-sm"
                  />
                </label>
                <label className="text-xs text-muted">
                  시도할 CPC 금액(원)
                  <input
                    type="number"
                    min={1}
                    value={bidValue}
                    onChange={(e) => setBidInputs((prev) => ({ ...prev, [c.campaign_id]: e.target.value }))}
                    className="mt-1 block w-28 rounded border border-border-subtle bg-surface px-2 py-1 text-sm"
                  />
                </label>
                <button
                  onClick={() => {
                    const n = Number(bidValue);
                    if (Number.isInteger(n) && n >= 1) handleApplyBid(c.campaign_id, n);
                  }}
                  disabled={runningCampaignId !== null}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  적용하기
                </button>
                {rank?.suggested_cpc != null && (
                  <p className="text-xs text-muted">
                    아직 목표({rank.target_rank}위)보다 낮아요({rank.current_rank}위). {won(rank.suggested_cpc)}으로
                    시도해보는 걸 추천해요.
                  </p>
                )}
                {rank?.rank_status === "normal" && (
                  <p className="text-xs text-success">목표를 달성했어요!</p>
                )}
              </div>
```

이어지는 `{c.points.length === 0 ? (...) : (...)}` 블록과 그 다음 닫는 `</div>`는 그대로 두되, `distanceRanks.map((c) => (` ... `))}`로 닫히던 화살표 함수를 `{distanceRanks.map((c) => { ... return (...); })}` 형태로 바꿨으므로, 맵 콜백의 마지막(`</div>` 다음)을 다음으로 교체한다:

```tsx
            </div>
          ))}
```

다음으로 교체:

```tsx
            </div>
            );
          })}
```

- [ ] **Step 6: 프론트엔드 타입체크 + 빌드 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Run: `cd frontend && npm run build`
Expected: 빌드 성공, `/ads` 라우트 포함 정상 생성

- [ ] **Step 7: 로컬 브라우저로 실동작 확인**

`LIVE_CRAWL_ENABLED=false`인 로컬 환경에서도 다음은 확인 가능하다:
1. `/ads` 페이지 진입 → "Mock 스냅샷"/"경쟁 예상 CPC" 문구가 화면 어디에도 없는지 확인
2. 4개 브랜드(치킨/찜·탕·찌개/고기·구이/백반·죽·국수) 행/블록이 전부 보이는지 확인(Task 2의 로컬 DB 반영이 끝난 상태 전제)
3. "목표 순위" 입력값을 바꾸고 포커스를 벗어나면(`onBlur`) 네트워크 탭에 `PATCH /ads/campaigns/{id}` 요청이 나가는지 확인
4. "적용하기" 클릭 시 확인 다이얼로그가 뜨고, 취소하면 아무 요청도 안 나가는지 확인

- [ ] **Step 8: 커밋**

```bash
git add "frontend/src/app/(app)/ads/page.tsx"
git commit -m "feat: 광고 순위 모니터링에서 Mock/경쟁CPC 문구 제거, 브랜드별 입찰 조정 UI 추가"
```

---

## 최종 확인 (모든 태스크 완료 후)

- [ ] `cd backend && .venv/bin/pytest -v` 전체 PASS
- [ ] `cd frontend && npx tsc --noEmit && npm run build` 전체 성공
- [ ] 로컬 브라우저에서 `/ads` 페이지 진입 → 4개 브랜드 전부 표시, Mock/경쟁CPC 문구 없음, 목표순위 인라인 수정 동작 확인
- [ ] (사용자 동의 하에) 실 계정에서 "적용하기" 전체 흐름 1회 라이브 검증 — 소액(현재 CPC + 10~30원 수준)으로 실제 반영 → 30초 대기 → 자동 재측정 → 추천 문구 표시까지 확인
