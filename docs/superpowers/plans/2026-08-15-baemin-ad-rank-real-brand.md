# 광고 순위 모니터링 — 치밥대장 실브랜드 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "광고 순위 모니터링" 화면의 캠페인 하나(치밥대장, shop_no=14804318)를 실제 배민 브랜드에 연결해서 반경별 실측 크롤이 `.env` 수동 편집 없이 자동으로 정확한 가게 정보를 쓰게 하고, "순위 현황"·"광고 성과" 카드도 그 캠페인만 실데이터로 보여준다.

**Architecture:** `ad_campaigns`에 nullable `shop_no` 컬럼을 추가해 캠페인 단위로 실데이터/Mock을 가른다. 새 스크레이퍼(`fetch_shop_info`)가 사장님광장 가게관리 화면에서 상호명·카테고리·주소·위도경도를 실측하고, 크롤 실행 함수(`_run_local_crawl`)가 `shop_no` 있는 캠페인이면 이 값을 크롤러 서브프로세스에 환경변수로 주입한다(카카오 지오코딩은 폴백으로만 남김). `/ads/performance`·`/ads/rank-monitoring`은 `shop_no` 유무로 분기해서 있으면 이미 실데이터인 `BrandAdClickMetric`/실측 `AdRankSnapshot`을 쓰고, 없으면 기존 Mock 경로 그대로 둔다.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL(schema.sql), Playwright(sync API, backend), Appium(crawler, 별도 프로세스), Next.js App Router.

## Global Constraints

- 이번 범위는 캠페인 id=1(치밥대장, shop_no=14804318) 하나뿐이다. 캠페인 id=2(닭갈비연구소 당고점)는 `shop_no`를 NULL로 두고 어떤 코드 경로도 건드리지 않는다 — 모든 신규 분기는 `shop_no is not None`을 조건으로 한다.
- "경쟁 예상 CPC"는 배민 어떤 화면에도 노출되지 않아 실측이 구조적으로 불가능하다 — 계속 추정치로 남기고 프론트에서 "(추정)"이라고 명시한다. 실측으로 바꾸려 하지 않는다.
- `crawler/`는 이 저장소의 나머지(FastAPI/Next.js/backend)와 별도 Python venv를 쓰는 독립 도구다 — `crawler/tests/`도 자체 pytest 스위트(`cd crawler && .venv/bin/pytest`)로 따로 돈다. `backend/`와 `crawler/` 양쪽에 걸친 태스크는 두 venv를 각각 정확히 써야 한다.
- `fetch_shop_info`(Playwright, backend)는 이 저장소 컨벤션대로 자동화된 pytest로 덮지 않는다 — 이미 실 계정으로 4개 브랜드 전부 라이브 검증했다(설계 문서 "조사 과정에서 확인된 사실" 참고).
- `shop_no` 있는 캠페인의 가게 정보 조회(로그인/`fetch_shop_info`)가 실패하면 크롤 자체를 하드 에러로 중단한다 — `crawler/.env`로 조용히 폴백하지 않는다(엉뚱한 가게를 실측한 결과가 치밥대장 결과로 저장되는 걸 막기 위해).
- 새 엔드포인트를 만들지 않는다 — 기존 `GET /ads/performance`, `GET /ads/rank-monitoring`, `POST /ads/rank-by-distance/run`을 확장한다.
- 이 프로젝트(`backend/`)는 Alembic을 쓰지 않는다 — `schema.sql`이 DB 정본이라 `ALTER TABLE`이 아니라 `CREATE TABLE` 문 자체를 수정한다.
- 참고 스펙: `docs/superpowers/specs/2026-08-15-baemin-ad-rank-real-brand-design.md`

---

### Task 1: 데이터 모델 — `ad_campaigns.shop_no` 컬럼 추가

**Files:**
- Modify: `schema.sql` (`ad_campaigns` CREATE TABLE 블록)
- Modify: `backend/app/models.py` (`AdCampaign` 모델)
- Test: `backend/tests/test_ads.py` (신규 테스트 추가)

**Interfaces:**
- Consumes: 없음.
- Produces: `AdCampaign.shop_no: str | None`(신규 필드). 이후 모든 태스크가 이 필드로 실데이터/Mock을 분기한다.

- [ ] **Step 1: `schema.sql` — `ad_campaigns` 블록에 컬럼 추가**

`schema.sql`에서 다음 블록을 찾는다:

```sql
-- 13. ad_campaigns — 광고 캠페인. stores 1:N
-- ----------------------------------------------------------------------------
CREATE TABLE ad_campaigns (
    id          BIGSERIAL PRIMARY KEY,
    store_id    BIGINT      NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category    VARCHAR(30) NOT NULL,               -- 노출 카테고리 (예: 치킨)
    current_cpc INT         NOT NULL CHECK (current_cpc >= 0),   -- 현재 클릭당 단가 (원)
    target_rank SMALLINT    NOT NULL CHECK (target_rank >= 1),   -- 목표 순위
    status      VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused'))
);
```

다음으로 교체:

```sql
-- 13. ad_campaigns — 광고 캠페인. stores 1:N
--     shop_no가 있으면(예: 치밥대장 = '14804318') 이 캠페인은 실제 배민
--     브랜드에 연결된 것으로 취급해 순위 현황/광고 성과/반경별 실측이
--     실데이터 경로를 탄다. NULL이면(대부분의 캠페인) 지금처럼 전부 Mock.
-- ----------------------------------------------------------------------------
CREATE TABLE ad_campaigns (
    id          BIGSERIAL PRIMARY KEY,
    store_id    BIGINT      NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    category    VARCHAR(30) NOT NULL,               -- 노출 카테고리 (예: 치킨)
    current_cpc INT         NOT NULL CHECK (current_cpc >= 0),   -- 현재 클릭당 단가 (원)
    target_rank SMALLINT    NOT NULL CHECK (target_rank >= 1),   -- 목표 순위
    status      VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    shop_no     VARCHAR(20)                          -- 실제 배민 shop_no (baemin_shop_brands.shop_no와 동일한 값), 실데이터 캠페인만
);
```

- [ ] **Step 2: `backend/app/models.py` — `AdCampaign`에 필드 추가**

`class AdCampaign` 블록의 `status: Mapped[str] = mapped_column(String(10), default="active")` 줄 다음, `store: Mapped[Store] = relationship()` 줄 앞에 삽입:

```python
    shop_no: Mapped[str | None] = mapped_column(String(20), default=None)
```

- [ ] **Step 3: 실패하는 테스트 작성**

`backend/tests/test_ads.py`의 `make_campaign` 헬퍼:

```python
def make_campaign(db_session, store, current_cpc=400, target_rank=3):
    campaign = AdCampaign(store_id=store.id, category="치킨", current_cpc=current_cpc, target_rank=target_rank, status="active")
    db_session.add(campaign)
    db_session.commit()
    return campaign
```

를 다음으로 교체(기존 호출부는 `shop_no` 인자를 안 넘기므로 그대로 동작):

```python
def make_campaign(db_session, store, current_cpc=400, target_rank=3, shop_no=None):
    campaign = AdCampaign(
        store_id=store.id, category="치킨", current_cpc=current_cpc, target_rank=target_rank,
        status="active", shop_no=shop_no,
    )
    db_session.add(campaign)
    db_session.commit()
    return campaign
```

파일 끝에 추가:

```python
def test_ad_campaign_shop_no_defaults_to_none(db_session, seeded_user):
    campaign = make_campaign(db_session, seeded_user["store"])
    assert campaign.shop_no is None


def test_ad_campaign_shop_no_can_be_set(db_session, seeded_user):
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    assert campaign.shop_no == "14804318"
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k shop_no`
Expected: FAIL — `TypeError: 'shop_no' is an invalid keyword argument for AdCampaign`

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k shop_no`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 6: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 7: 로컬 검증 DB에 스키마 재적용 + 치밥대장 캠페인 연결**

```bash
docker exec baemin-verify-db2 psql -U postgres -d delivery_insight -c "
ALTER TABLE ad_campaigns ADD COLUMN shop_no VARCHAR(20);
UPDATE ad_campaigns SET shop_no = '14804318' WHERE id = 1;
"
docker exec baemin-verify-db2 psql -U postgres -d delivery_insight -c "select id, category, shop_no from ad_campaigns"
```

Expected: id=1 행에 `shop_no='14804318'`, id=2 행은 `shop_no` NULL로 나온다.

- [ ] **Step 8: 커밋**

```bash
git add schema.sql backend/app/models.py backend/tests/test_ads.py
git commit -m "feat: ad_campaigns에 실제 배민 브랜드 연결용 shop_no 컬럼 추가"
```

---

### Task 2: 크롤러 설정 — 환경변수 우선순위 + 위도경도 직접 주입

**Files:**
- Modify: `crawler/config.py`
- Modify: `crawler/run_crawl.py`
- Test: `crawler/tests/test_config.py` (신규)

**Interfaces:**
- Consumes: 없음(외부 의존 없는 순수 로직 변경).
- Produces: `Settings`에 `store_lat: float | None`, `store_lng: float | None` 필드 추가. `load_settings()`가 `.env` 파일보다 `os.environ`을 우선한다. Task 4가 이 우선순위를 이용해 백엔드에서 넘긴 환경변수로 크롤러를 실행한다.

이 태스크는 `backend/`가 아니라 `crawler/`(독립 Python venv)에서 작업한다 — 아래 모든 pytest 명령은 `cd crawler && .venv/bin/pytest`로 실행한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`crawler/tests/test_config.py` 신규 생성:

```python
import os

import pytest

from config import load_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("KAKAO_REST_API_KEY", "STORE_ADDRESS", "STORE_DISPLAY_NAME", "CATEGORY_LABEL", "STORE_LAT", "STORE_LNG"):
        monkeypatch.delenv(key, raising=False)


def test_load_settings_reads_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAKAO_REST_API_KEY=file-key\n"
        "STORE_ADDRESS=서울시 노원구 당고개로 1\n"
        "STORE_DISPLAY_NAME=치킨대장 당고점\n"
        "CATEGORY_LABEL=치킨\n"
    )
    settings = load_settings(str(env_file))
    assert settings.kakao_api_key == "file-key"
    assert settings.store_display_name == "치킨대장 당고점"
    assert settings.store_lat is None
    assert settings.store_lng is None


def test_load_settings_prefers_process_env_over_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAKAO_REST_API_KEY=file-key\n"
        "STORE_ADDRESS=서울시 노원구 당고개로 1\n"
        "STORE_DISPLAY_NAME=파일 값\n"
        "CATEGORY_LABEL=치킨\n"
    )
    monkeypatch.setenv("STORE_DISPLAY_NAME", "프로세스 환경변수 값")
    settings = load_settings(str(env_file))
    assert settings.store_display_name == "프로세스 환경변수 값"  # 파일 값이 아니라 환경변수가 이겨야 함


def test_load_settings_picks_up_store_lat_lng_from_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAKAO_REST_API_KEY=file-key\n"
        "STORE_ADDRESS=서울시 노원구 당고개로 1\n"
        "STORE_DISPLAY_NAME=치킨대장 당고점\n"
        "CATEGORY_LABEL=치킨\n"
    )
    monkeypatch.setenv("STORE_LAT", "37.667646")
    monkeypatch.setenv("STORE_LNG", "127.079584")
    settings = load_settings(str(env_file))
    assert settings.store_lat == 37.667646
    assert settings.store_lng == 127.079584


def test_load_settings_missing_required_value_raises(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("KAKAO_REST_API_KEY=file-key\n")
    with pytest.raises(RuntimeError, match=r"\.env에 다음 값이 없습니다"):
        load_settings(str(env_file))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd crawler && .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `test_load_settings_prefers_process_env_over_file`와 `test_load_settings_picks_up_store_lat_lng_from_env`가 실패(현재는 `.env` 파일 값만 읽고 `store_lat`/`store_lng` 필드 자체가 없음). 다른 2개는 이미 통과할 수 있음.

- [ ] **Step 3: `crawler/config.py` 수정**

파일 전체를 다음으로 교체:

```python
"""crawler 전역 설정. 프로세스 환경변수를 우선하고, 없는 값만 .env 파일에서
채운다(표준 dotenv 우선순위 관례) — 백엔드가 실제 배민 브랜드 정보를
환경변수로 주입해 크롤러를 실행할 때(ads.py의 _run_local_crawl 참고) .env
파일을 건드리지 않고도 그 값이 우선 적용되게 하기 위함이다. 개발자가
크롤러만 단독으로 실행할 땐(환경변수 없음) 지금처럼 .env 파일 값을 그대로
쓴다."""

import os
from dataclasses import dataclass

from dotenv import dotenv_values

# 가게 주소를 기점으로 하는 반경 구간(사용자 확정) — 매 구간마다 가게 주소부터
# 다시 계산한 랜덤 지점 1개씩을 뽑는다. 0km(가게 주소 자체)는 run_crawl.py에서
# 별도로 처리한다.
RING_KM_RANGES = [(1.5, 2.5), (2.5, 3.5)]


@dataclass(frozen=True)
class Settings:
    kakao_api_key: str
    store_address: str
    store_display_name: str
    category_label: str
    store_lat: float | None = None
    store_lng: float | None = None


def _get(env_file_values: dict, key: str) -> str | None:
    """프로세스 환경변수(os.environ)를 .env 파일 값보다 우선한다."""
    return os.environ.get(key) or env_file_values.get(key) or None


def load_settings(env_path: str = ".env") -> Settings:
    file_values = dotenv_values(env_path)
    resolved = {
        k: _get(file_values, k)
        for k in ("KAKAO_REST_API_KEY", "STORE_ADDRESS", "STORE_DISPLAY_NAME", "CATEGORY_LABEL")
    }
    missing = [k for k, v in resolved.items() if not v]
    if missing:
        raise RuntimeError(f".env에 다음 값이 없습니다: {', '.join(missing)}")

    lat_str = _get(file_values, "STORE_LAT")
    lng_str = _get(file_values, "STORE_LNG")

    return Settings(
        kakao_api_key=resolved["KAKAO_REST_API_KEY"],
        store_address=resolved["STORE_ADDRESS"],
        store_display_name=resolved["STORE_DISPLAY_NAME"],
        category_label=resolved["CATEGORY_LABEL"],
        store_lat=float(lat_str) if lat_str else None,
        store_lng=float(lng_str) if lng_str else None,
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd crawler && .venv/bin/pytest tests/test_config.py -v`
Expected: 4개 테스트 전부 PASS

- [ ] **Step 5: `crawler/run_crawl.py` — 위도경도 있으면 지오코딩 건너뜀**

```python
    try:
        base_lat, base_lng = address_to_coords(settings.store_address, settings.kakao_api_key)
    except GeocodeError as e:
        print(f"지오코딩 실패, 실행을 중단합니다: {e}")
        sys.exit(1)
```

를 다음으로 교체:

```python
    if settings.store_lat is not None and settings.store_lng is not None:
        base_lat, base_lng = settings.store_lat, settings.store_lng
    else:
        try:
            base_lat, base_lng = address_to_coords(settings.store_address, settings.kakao_api_key)
        except GeocodeError as e:
            print(f"지오코딩 실패, 실행을 중단합니다: {e}")
            sys.exit(1)
```

- [ ] **Step 6: 크롤러 전체 테스트 재확인**

Run: `cd crawler && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음 — `test_geocode.py` 등 기존 테스트도 그대로 통과)

- [ ] **Step 7: 커밋**

```bash
git add crawler/config.py crawler/run_crawl.py crawler/tests/test_config.py
git commit -m "feat: 크롤러 설정이 프로세스 환경변수를 .env 파일보다 우선하고 위도경도 직접 주입을 지원하도록 변경"
```

---

### Task 3: 백엔드 — 가게 정보 실측 스크레이퍼 `fetch_shop_info`

**Files:**
- Modify: `backend/scrapers/baemin_stats.py`

**Interfaces:**
- Consumes: `baemin_auth.login()`이 반환한 `BaeminSession.page`(이미 인증된 살아있는 Playwright 페이지) — 재로그인하지 않는다.
- Produces: `fetch_shop_info(page, shop_no: str) -> dict`(반환 키: `name`, `category`, `road_address`, `latitude`, `longitude`), `BaeminStatsScrapeError`(이미 있음, 재사용). Task 4가 이 함수를 그대로 가져다 쓴다.

이 태스크는 이미 계획 수립 과정에서 실 계정으로 4개 브랜드 전부 라이브 조사를 마쳤다 — "조사"가 아니라 "이미 확정된 조사 결과를 코드로 옮기는" 태스크다. 확인된 사실(설계 문서 "조사 과정에서 확인된 사실" 절과 동일):

- 화면 URL은 `https://self.baemin.com/shops/{shopNo}/manage`로 직접 이동 가능하다.
- 이 화면이 `GET /v4/store/shops/{shopNo}`를 organic하게 호출하고, 응답에 `name`(문자열), `categories`(리스트, `categories[0].name`이 배민 카테고리 탭과 정확히 일치하는 문자열), `address.road.address`(도로명주소 문자열), `address.latitude`/`address.longitude`(숫자)가 그대로 들어있다(2026-08-15 실측 확인 — 치밥대장 기준 `name: "치밥대장 숯불양념92치킨 노원당고개점"`, `categories[0].name: "치킨"`, `address.road.address: "서울특별시 노원구 덕릉로118길 11"`, `latitude: 37.667646`, `longitude: 127.079584`).

- [ ] **Step 1: `backend/scrapers/baemin_stats.py`에 `fetch_shop_info` 추가**

파일 끝(`fetch_settlement_breakdown_details` 함수 다음)에 추가:

```python


def fetch_shop_info(page, shop_no: str) -> dict:
    """사장님광장 가게관리 화면(`/shops/{shop_no}/manage`)에서
    `GET /v4/store/shops/{shop_no}` organic 응답을 가로채 상호명·카테고리·
    도로명주소·위도·경도를 반환한다. 광고 순위 실측 크롤러(`crawler/`)가
    가게 정보를 `.env` 수동 편집 대신 이 함수의 결과로 받을 수 있게
    하기 위한 용도다(호출자는 `backend/app/routers/ads.py`의
    `_run_local_crawl` 참고).

    반환 키: `name`(str), `category`(str, 배민 카테고리 탭과 정확히
    일치하는 문자열), `road_address`(str), `latitude`(float),
    `longitude`(float)."""
    body_holder: dict = {}
    observed = {"any": False}

    def _on_response(response) -> None:
        if urlparse(response.url).path != f"/v4/store/shops/{shop_no}":
            return
        observed["any"] = True
        if response.status == 200:
            try:
                body_holder["body"] = response.json()
            except Exception:
                pass

    page.on("response", _on_response)
    try:
        try:
            page.goto(f"https://self.baemin.com/shops/{shop_no}/manage")
        except Exception as e:
            raise BaeminStatsScrapeError(f"가게관리 페이지 이동에 실패했습니다: {e}") from e

        page.wait_for_timeout(3_000)
        _dismiss_backdrop_if_present(page)
        page.wait_for_timeout(1_000)
    finally:
        page.remove_listener("response", _on_response)

    if not observed["any"]:
        raise BaeminStatsScrapeError("가게 정보 API 응답을 한 번도 확인하지 못했습니다")
    if "body" not in body_holder:
        raise BaeminStatsScrapeError("가게 정보 API 응답을 받았지만 파싱하지 못했습니다")

    body = body_holder["body"]
    try:
        categories = body["categories"]
        address = body["address"]
        return {
            "name": body["name"],
            "category": categories[0]["name"],
            "road_address": address["road"]["address"],
            "latitude": float(address["latitude"]),
            "longitude": float(address["longitude"]),
        }
    except (KeyError, IndexError) as e:
        raise BaeminStatsScrapeError(f"가게 정보 응답 형태가 예상과 다릅니다: {e}") from e
```

- [ ] **Step 2: 실 계정으로 동작 재검증**

이 태스크의 Global Constraints에 따라 화면 상호작용 자체는 자동화된 pytest로 덮지 않는다 — 이미 계획 수립 과정에서 4개 브랜드 전부 라이브로 검증했지만, 코드로 옮기는 과정에서 오타가 있을 수 있으므로 실 계정으로 최종 확인한다:

```bash
cd backend
.venv/bin/python -c "
import os
os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:15432/delivery_insight')
os.environ.setdefault('CREDENTIAL_ENCRYPTION_KEY', '<로컬 검증용 .env의 CREDENTIAL_ENCRYPTION_KEY 값>')
from sqlalchemy import create_engine, text
from app.credential_crypto import decrypt_credential
from scrapers.baemin_auth import login
from scrapers.baemin_stats import fetch_shop_info

engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    ciphertext = conn.execute(text(\"select credential_ciphertext from store_platform_connections where id = 6\")).one()[0]
cred = decrypt_credential(ciphertext)

session = login(cred['login_id'], cred['password'])
info = fetch_shop_info(session.page, '14804318')
print(info)
assert info['name'] == '치밥대장 숯불양념92치킨 노원당고개점'
assert info['category'] == '치킨'
assert info['road_address'] == '서울특별시 노원구 덕릉로118길 11'
assert abs(info['latitude'] - 37.667646) < 0.0001
assert abs(info['longitude'] - 127.079584) < 0.0001
print('검증 통과')
session.close()
"
```
Expected: `검증 통과` 출력, 에러 없음.

- [ ] **Step 3: 전체 백엔드 테스트 재확인 (회귀 없음 확인용 — 이 태스크는 새 자동 테스트를 추가하지 않는다)**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 4: 커밋**

```bash
git add backend/scrapers/baemin_stats.py
git commit -m "feat: 사장님광장 가게관리 화면에서 상호명/카테고리/주소/좌표를 가로채는 fetch_shop_info 추가"
```

---

### Task 4: 백엔드 — 크롤 실행에 실측 가게 정보 주입

**Files:**
- Modify: `backend/app/routers/ads.py`

**Interfaces:**
- Consumes: Task 1의 `AdCampaign.shop_no`. Task 3의 `fetch_shop_info`, `BaeminStatsScrapeError`(이미 import돼 있을 수도, 없으면 추가). 기존 `app.credential_crypto.decrypt_credential`, `scrapers.baemin_auth.login`(둘 다 이미 이 저장소에 있음, 새로 안 만듦).
- Produces: 없음(내부 오케스트레이션 — `_run_local_crawl`의 동작 변경).

- [ ] **Step 1: import 추가**

`backend/app/routers/ads.py` 상단 import 블록에 추가(기존 import는 그대로 유지):

```python
from app.credential_crypto import decrypt_credential
from app.models import StorePlatformConnection
from scrapers.baemin_auth import login as baemin_login
from scrapers.baemin_stats import fetch_shop_info
```

(`app.models`의 기존 import 줄 `from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, BrandAdClickMetric, Order, Platform, Store, User`에 `StorePlatformConnection`을 추가해도 된다 — 별도 줄로 추가하든 합치든 최종적으로 `StorePlatformConnection`이 import돼 있으면 된다.)

- [ ] **Step 2: `_run_local_crawl`에 가게 정보 주입 단계 추가**

```python
def _run_local_crawl(campaign_id: int) -> tuple[int, int]:
    """이 프로세스와 같은 컴퓨터의 crawler venv/에뮬레이터로 실제 크롤링을 실행하고
    결과를 DB에 적재한다. (inserted, skipped) 개수를 반환한다. 3~5분 걸리는
    블로킹 호출이므로 반드시 백그라운드 스레드에서만 부른다(요청 핸들러에서
    직접 부르면 배포 환경 프록시 타임아웃에 걸린다 — 아래 _start_crawl_job 참고)."""
    if not _CRAWLER_PYTHON.exists():
        raise HTTPException(500, f"crawler venv를 찾을 수 없습니다: {_CRAWLER_PYTHON}")
    try:
        proc = subprocess.run(
            [str(_CRAWLER_PYTHON), "run_crawl.py"],
            cwd=_CRAWLER_DIR,
            env=_crawler_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=_CRAWL_TIMEOUT_SEC,
        )
```

를 다음으로 교체:

```python
def _run_local_crawl(campaign_id: int) -> tuple[int, int]:
    """이 프로세스와 같은 컴퓨터의 crawler venv/에뮬레이터로 실제 크롤링을 실행하고
    결과를 DB에 적재한다. (inserted, skipped) 개수를 반환한다. 3~5분 걸리는
    블로킹 호출이므로 반드시 백그라운드 스레드에서만 부른다(요청 핸들러에서
    직접 부르면 배포 환경 프록시 타임아웃에 걸린다 — 아래 _start_crawl_job 참고).

    캠페인에 shop_no가 있으면(실데이터 캠페인, 지금은 치밥대장뿐) 크롤러
    실행 전에 이 프로세스가 이미 갖고 있는 배민 인증 흐름으로 로그인해
    fetch_shop_info로 실제 상호명/카테고리/주소/좌표를 가져와 크롤러
    서브프로세스의 환경변수로 넘긴다 — crawler/.env 파일은 건드리지 않는다.
    이 단계가 실패하면 크롤 자체를 하드 에러로 중단한다(.env로 조용히
    폴백하면 엉뚱한 가게를 실측한 결과가 이 캠페인 결과로 저장될 위험이
    있다). shop_no가 없는 캠페인(예: 닭갈비연구소)은 이 단계를 완전히
    건너뛰고 기존처럼 crawler/.env 값을 그대로 쓴다."""
    if not _CRAWLER_PYTHON.exists():
        raise HTTPException(500, f"crawler venv를 찾을 수 없습니다: {_CRAWLER_PYTHON}")

    env = _crawler_subprocess_env()
    db = SessionLocal()
    try:
        campaign = db.get(AdCampaign, campaign_id)
        if campaign is not None and campaign.shop_no:
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
                    info = fetch_shop_info(session.page, campaign.shop_no)
                finally:
                    session.close()
            except Exception as e:
                raise HTTPException(502, f"가게 정보 조회에 실패해 크롤을 시작하지 않았습니다: {e}") from e
            env["STORE_DISPLAY_NAME"] = info["name"]
            env["CATEGORY_LABEL"] = info["category"]
            env["STORE_ADDRESS"] = info["road_address"]
            env["STORE_LAT"] = str(info["latitude"])
            env["STORE_LNG"] = str(info["longitude"])
    finally:
        db.close()

    try:
        proc = subprocess.run(
            [str(_CRAWLER_PYTHON), "run_crawl.py"],
            cwd=_CRAWLER_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=_CRAWL_TIMEOUT_SEC,
        )
```

(이 아래 `except subprocess.TimeoutExpired:`부터 함수 끝까지는 기존 코드 그대로 — 변경 없음. `env=_crawler_subprocess_env()`였던 인자만 `env=env`로 바뀐 것에 주의.)

`backend/app/routers/ads.py` 상단에 `from app.db import SessionLocal`이 아직 없다면 추가한다(`from app.db import get_db` 옆에 같이 두거나 그 줄을 `from app.db import SessionLocal, get_db`로 합친다).

- [ ] **Step 3: 전체 백엔드 테스트 재확인 (회귀 없음 확인용 — Playwright/subprocess 경로라 이 스텝에서 새 자동 테스트는 추가하지 않는다)**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS

- [ ] **Step 4: 실 계정으로 전체 흐름 재검증 (에뮬레이터 + Appium 필요)**

로컬 에뮬레이터·Appium 서버가 켜져 있는 상태에서(꺼져있다면 `crawler/start_worker_services.sh`의 1~2단계와 동일하게 `emulator -avd baemin_test`, `appium`을 먼저 띄운다), 로컬 백엔드(`:8000`)와 프론트(`:3000`)를 실행한 뒤 "광고 순위 모니터링" 화면에서 치밥대장(캠페인 id=1) 카드의 "우리가게 순위 확인"을 눌러 폴링이 끝날 때까지 기다린다.

Expected: 크롤이 시작되기 전에 `crawler/.env`의 기존 값(다른 가게 정보)이 아니라 치밥대장의 실제 정보로 실행됐는지 `crawler/logs/`의 최근 실행 로그나 `crawler/output/results.csv`의 `category` 컬럼("치킨") 및 스크린샷으로 확인한다. 정상 완료되면 `ad_rank_snapshots`에 `distance_km IS NOT NULL`인 새 행 3개(0km/1.5~2.5km/2.5~3.5km)가 캠페인 id=1로 적재된다.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/ads.py
git commit -m "feat: shop_no 있는 캠페인의 크롤 실행 시 실측 가게 정보를 자동 주입"
```

---

### Task 5: 백엔드 — `/ads/performance`, `/ads/rank-monitoring` 실데이터 분기

**Files:**
- Modify: `backend/app/routers/ads.py`
- Test: `backend/tests/test_ads.py`

**Interfaces:**
- Consumes: Task 1의 `AdCampaign.shop_no`. 기존 `BrandAdClickMetric` 모델, `app.acos.calculate_performance`(둘 다 이미 있음, `/ads/click-performance`가 쓰는 것과 동일).
- Produces: 없음(기존 두 엔드포인트의 응답 내용만 `shop_no` 유무에 따라 달라짐 — 응답 JSON의 키 구조는 그대로 유지).

- [ ] **Step 1: `GET /ads/performance` 실패하는 테스트 작성**

`backend/tests/test_ads.py` 파일 끝에 추가:

```python
def test_ads_performance_uses_real_brand_click_metrics_when_shop_no_set(client, db_session, seeded_user, platforms, auth_headers):
    from app.models import BrandAdClickMetric
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804318", metric_date=date.today(),
        ad_spend=34730, impressions=4632, clicks=106, ad_orders=16, ad_revenue=427000,
    ))
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["campaign_id"] == campaign.id
    # CPC = 34730 / 106 ≈ 327.64 (실측 브랜드 데이터에서 계산됨, Mock ad_performance_metrics 아님)
    assert row["cpc"] == round(34730 / 106, 2)
    assert row["ad_spend"] == 34730


def test_ads_performance_ignores_ad_performance_metrics_when_shop_no_set(client, db_session, seeded_user, platforms, auth_headers):
    from app.models import BrandAdClickMetric
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    db_session.add(AdPerformanceMetric(
        campaign_id=campaign.id, metric_date=date.today(),
        ad_spend=999999, clicks=1, ad_orders=0, ad_revenue=0,
    ))
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804318", metric_date=date.today(),
        ad_spend=1000, impressions=100, clicks=10, ad_orders=1, ad_revenue=25000,
    ))
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["ad_spend"] == 1000  # AdPerformanceMetric(999999)이 아니라 BrandAdClickMetric 값


def test_ads_performance_without_shop_no_still_uses_mock(client, db_session, seeded_user, auth_headers):
    """회귀 테스트 — shop_no 없는 캠페인은 이번 변경으로 전혀 영향받지 않는다."""
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add(AdPerformanceMetric(
        campaign_id=campaign.id, metric_date=date.today(),
        ad_spend=10_000, clicks=100, ad_orders=10, ad_revenue=200_000,
    ))
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["ad_spend"] == 10_000
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k "real_brand_click_metrics or ignores_ad_performance_metrics"`
Expected: FAIL — `shop_no`를 무시하고 항상 `AdPerformanceMetric`(Mock)만 집계하므로 `cpc`/`ad_spend` 값이 기대와 다르게 나옴.

- [ ] **Step 3: `GET /ads/performance` 수정**

`backend/app/routers/ads.py`의 `ads_performance` 함수 전체를 다음으로 교체:

```python
@router.get("/ads/performance")
def ads_performance(
    store_id: int | None = None,
    days: int = Query(14, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    campaigns = db.scalars(select(AdCampaign).where(AdCampaign.store_id == sid)).all()
    since = date.today() - timedelta(days=days)

    total_orders = db.scalar(
        select(func.count(Order.id)).where(Order.store_id == sid, Order.ordered_at >= since)
    )

    result = []
    for c in campaigns:
        if c.shop_no:
            # shop_no가 있으면(실데이터 캠페인) 이미 실측인 BrandAdClickMetric을
            # 쓴다 — /ads/click-performance가 하는 것과 동일한 조회, Mock인
            # ad_performance_metrics는 아예 조회하지 않는다.
            baemin_platform = db.scalar(select(Platform).where(Platform.code == "baemin"))
            agg = db.execute(
                select(
                    func.coalesce(func.sum(BrandAdClickMetric.ad_spend), 0),
                    func.coalesce(func.sum(BrandAdClickMetric.clicks), 0),
                    func.coalesce(func.sum(BrandAdClickMetric.ad_orders), 0),
                    func.coalesce(func.sum(BrandAdClickMetric.ad_revenue), 0),
                ).where(
                    BrandAdClickMetric.store_id == sid,
                    BrandAdClickMetric.platform_id == baemin_platform.id,
                    BrandAdClickMetric.shop_no == c.shop_no,
                    BrandAdClickMetric.metric_date >= since,
                )
            ).one() if baemin_platform else (0, 0, 0, 0)
        else:
            agg = db.execute(
                select(
                    func.coalesce(func.sum(AdPerformanceMetric.ad_spend), 0),
                    func.coalesce(func.sum(AdPerformanceMetric.clicks), 0),
                    func.coalesce(func.sum(AdPerformanceMetric.ad_orders), 0),
                    func.coalesce(func.sum(AdPerformanceMetric.ad_revenue), 0),
                ).where(AdPerformanceMetric.campaign_id == c.id, AdPerformanceMetric.metric_date >= since)
            ).one()
        perf = calculate_performance(*agg)
        order_share = round(perf.ad_orders / total_orders, 4) if total_orders else None
        result.append({
            "campaign_id": c.id,
            "category": c.category,
            "current_cpc": c.current_cpc,
            "status": c.status,
            "period_days": days,
            "ad_spend": perf.ad_spend,
            "clicks": perf.clicks,
            "ad_orders": perf.ad_orders,
            "ad_revenue": perf.ad_revenue,
            "cpc": perf.cpc,
            "cvr": perf.cvr,
            "aov": perf.aov,
            "acos": perf.acos,
            "score": perf.score,
            "order_share": order_share,
        })
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k "real_brand_click_metrics or ignores_ad_performance_metrics or without_shop_no_still_uses_mock"`
Expected: 3개 테스트 전부 PASS

- [ ] **Step 5: `GET /ads/rank-monitoring` 실패하는 테스트 작성**

`backend/tests/test_ads.py` 파일 끝에 추가:

```python
def test_rank_monitoring_uses_real_distance_snapshot_when_shop_no_set(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3, shop_no="14804318")
    db_session.add_all([
        # 시간별 Mock 스냅샷(distance_km NULL) — shop_no 있는 캠페인이면 무시돼야 함
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
                        current_rank=1, competitor_est_cpc=390, status="normal", recommended_action="keep"),
        # 반경별 실측 스냅샷(distance_km NOT NULL) — 0km가 "현재 순위"의 근거가 돼야 함
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc),
                        current_rank=36, distance_km=0, point_label="0km", total_scanned=36, ads_above=8),
    ])
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] == 36  # 시간별 Mock(1위)이 아니라 실측 0km(36위)
    assert row["rank_status"] == "rank_dropped"  # 36 > target_rank(3)
    assert row["recommended_action"] == "raise_cpc"
    assert row["suggested_cpc"] is None  # 경쟁 CPC를 몰라 구체적 액수는 못 줌


def test_rank_monitoring_no_real_snapshot_yet_when_shop_no_set(client, db_session, seeded_user, auth_headers):
    make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] is None
    assert row["recommended_action"] == "keep"


def test_rank_monitoring_without_shop_no_still_uses_mock_snapshot(client, db_session, seeded_user, auth_headers):
    """회귀 테스트 — shop_no 없는 캠페인은 기존 시간별 Mock 스냅샷 로직 그대로."""
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3)
    db_session.add(AdRankSnapshot(
        campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc),
        current_rank=7, competitor_est_cpc=650, status="rank_dropped",
        recommended_action="raise_cpc", suggested_cpc=700,
    ))
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] == 7
    assert row["suggested_cpc"] == 700  # Mock 경로는 suggested_cpc를 그대로 줌
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k "real_distance_snapshot or no_real_snapshot_yet"`
Expected: FAIL — 지금은 `shop_no`를 무시하고 항상 가장 최근 스냅샷(시간별 Mock 포함)을 그대로 반환하므로 `current_rank`가 1로 나와 기대(36)와 다름.

- [ ] **Step 7: `GET /ads/rank-monitoring` 수정**

`backend/app/routers/ads.py`의 `ads_rank_monitoring` 함수 전체를 다음으로 교체:

```python
@router.get("/ads/rank-monitoring")
def ads_rank_monitoring(
    store_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = store_id or get_user_default_store_id(user, db)
    campaigns = db.scalars(select(AdCampaign).where(AdCampaign.store_id == sid)).all()

    result = []
    for c in campaigns:
        if c.shop_no:
            # shop_no가 있으면(실데이터 캠페인) 시간별 Mock 스냅샷이 아니라
            # 반경별 실측(distance_km=0, 가게 주소 지점)의 가장 최근 값을
            # "현재 순위"로 쓴다 — competitor_est_cpc는 실측이 구조적으로
            # 불가능해 계속 추정치로 남긴다(아래).
            real_latest = db.scalar(
                select(AdRankSnapshot)
                .where(AdRankSnapshot.campaign_id == c.id, AdRankSnapshot.distance_km == 0)
                .order_by(AdRankSnapshot.snapshot_at.desc())
                .limit(1)
            )
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

        latest = db.scalar(
            select(AdRankSnapshot)
            .where(AdRankSnapshot.campaign_id == c.id)
            .order_by(AdRankSnapshot.snapshot_at.desc())
            .limit(1)
        )
        result.append({
            "campaign_id": c.id,
            "category": c.category,
            "current_cpc": c.current_cpc,
            "target_rank": c.target_rank,
            "status": c.status,
            "current_rank": latest.current_rank if latest else None,
            "competitor_est_cpc": latest.competitor_est_cpc if latest else None,
            "rank_status": latest.status if latest else None,
            "recommended_action": latest.recommended_action if latest else "keep",
            "suggested_cpc": latest.suggested_cpc if latest else None,
            "snapshot_at": latest.snapshot_at.isoformat() if latest else None,
        })
    return result
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v -k "real_distance_snapshot or no_real_snapshot_yet or without_shop_no_still_uses_mock_snapshot"`
Expected: 3개 테스트 전부 PASS

- [ ] **Step 9: 전체 백엔드 테스트 재확인**

Run: `cd backend && .venv/bin/pytest -v`
Expected: 전체 PASS (회귀 없음)

- [ ] **Step 10: 커밋**

```bash
git add backend/app/routers/ads.py backend/tests/test_ads.py
git commit -m "feat: /ads/performance, /ads/rank-monitoring이 shop_no 있는 캠페인은 실데이터로 응답"
```

---

### Task 6: 프론트엔드 — 추정치 라벨 + 안내 문구 갱신

**Files:**
- Modify: `frontend/src/app/(app)/ads/page.tsx`

**Interfaces:**
- Consumes: Task 5의 `/ads/performance`, `/ads/rank-monitoring` 응답(응답 JSON 키 구조는 안 바뀜 — 프론트 타입 변경 없음).
- Produces: 없음(터미널 UI 컴포넌트).

- [ ] **Step 1: 상단 안내 문구 갱신**

```tsx
        <p className="text-sm text-muted">
          순위 현황·경쟁 CPC는 수집됐다고 가정한 Mock 스냅샷입니다. 아래 반경별 순위만
          실기기 자동화로 실측한 값이며, 사이트가 요청마다 직접 크롤링하지는 않습니다.
          CPC 자동 입찰은 하지 않습니다.
        </p>
```

를 다음으로 교체:

```tsx
        <p className="text-sm text-muted">
          치밥대장은 실제 배민 데이터 기반입니다 — 현재 순위는 아래 반경별 실측(실기기
          자동화) 중 가게 주소 지점(0km) 결과, 광고 성과는 우리가게클릭 실데이터입니다.
          경쟁 가게 CPC만은 배민이 노출하지 않아 추정치입니다. 나머지 캠페인은 수집됐다고
          가정한 Mock 스냅샷입니다. CPC 자동 입찰은 하지 않습니다.
        </p>
```

- [ ] **Step 2: "경쟁 예상 CPC" 컬럼 헤더에 "(추정)" 표시**

```tsx
                <th className="font-medium">경쟁 예상 CPC</th>
```

를 다음으로 교체:

```tsx
                <th className="font-medium">경쟁 예상 CPC (추정)</th>
```

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 로컬에서 실제로 확인**

로컬 백엔드(`:8000`)/프론트(`:3000`) 실행 후(Task 4의 실 계정 크롤 재검증이 이미 끝나 `ad_rank_snapshots`에 치밥대장 실측 데이터가 있다는 전제), "광고 순위 모니터링" 화면에서 치밥대장 카드가 순위 현황/광고 성과 모두 실측값으로, 닭갈비연구소 카드는 그대로 Mock으로 나오는지 확인한다.

- [ ] **Step 5: 커밋**

```bash
git add "frontend/src/app/(app)/ads/page.tsx"
git commit -m "feat: 광고 순위 모니터링 화면에 실측/추정 안내 문구 반영"
```

---

## CLAUDE.md 갱신 (마지막 태스크 이후, 최종 리뷰 전)

"창의 기능: 광고 순위 모니터링" 절에, 설계 문서의 "CLAUDE.md 갱신" 절
내용대로 캠페인-브랜드 연결과 가게 정보 자동 조회가 치밥대장 하나에 한해
실측 기반으로 바뀌었다는 내용을 추가한다. 별도 태스크로 분리하지 않고
Task 6 완료 후 전체 리뷰 단계에서 함께 처리한다(이전 실데이터 연동
계획들과 동일한 관례).
