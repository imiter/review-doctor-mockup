# 광고 순위 모니터링 — 치밥대장 실브랜드 연동 설계

날짜: 2026-08-15
관련 결정: CLAUDE.md "방향 전환" 로드맵 3번의 연장선이자, "창의 기능: 광고
순위 모니터링"(반경별 실측)의 확장. 지금까지 `ad_campaigns`는 시드
Mock 매장(치킨대장 당고점/닭갈비연구소 당고점) 기준이었고, 반경별 실측
크롤러(`crawler/`)는 `.env` 파일에 수동으로 적어넣은 가게 정보로만
동작했다. 이번엔 실제 배민 브랜드 하나(치밥대장, shop_no=14804318)를
캠페인에 연결해서 이 흐름 전체를 실데이터로 바꾼다. 사용자 결정
(2026-08-15)에 따라 나머지 3개 브랜드(블랙닭갈비/곱도리탕/행복가성비
컵밥)와 닭갈비연구소 당고점 캠페인은 이번 범위에서 완전히 제외한다 —
지금처럼 그대로 둔다.

## 배경 / 목적

"광고 순위 모니터링" 화면(`/ads`)엔 카드가 3개 있다 — 순위 현황, 반경별
실측 순위, 광고 성과. 지금 상태:
- **반경별 실측 순위**는 이미 실측(Appium 실기기 크롤러)이지만, 가게
  정보(주소/상호명/카테고리)를 `crawler/.env`에서 수동으로 읽어서 어느
  캠페인을 눌러도 항상 같은 하나의(지금은 개발자가 테스트용으로 넣어둔
  다른 가게) 정보로 크롤링한다 — 캠페인과 실제로 연결돼 있지 않다.
- **순위 현황**과 **광고 성과**는 전부 Mock(`ad_campaigns`,
  `ad_performance_metrics`, `ad_rank_snapshots`의 `distance_km IS NULL`
  스냅샷)이다.

## 조사 과정에서 확인된 사실 (중요)

실 계정으로 사장님광장 각 브랜드의 "가게 관리" 화면
(`/shops/{shopNo}/manage`)을 열어 `GET /v4/store/shops/{shopNo}` 응답을
직접 캡처했다(2026-08-15):

- 응답에 `name`(정확한 상호명), `categories[0].name`(배민 카테고리
  탭과 동일한 정확한 문자열), `address.road.address`(도로명주소),
  `address.latitude`/`address.longitude`(위도/경도)가 전부 들어있다.
  4개 브랜드 전부 확인했고, 치밥대장(shop_no=14804318)은
  `name: "치밥대장 숯불양념92치킨 노원당고개점"`,
  `categories[0].name: "치킨"`,
  `road.address: "서울특별시 노원구 덕릉로118길 11"`,
  `latitude: 37.667646, longitude: 127.079584`로 확인됐다.
- 4개 브랜드 전부 같은 주소였다(한 주방에서 여러 브랜드로 배달하는
  구조로 보인다) — 이번 범위에선 치밥대장 하나만 쓰지만 참고로 기록.
- `crawler/geo_sampling.py`의 반경 구간 샘플링(`sample_ring_point`)은
  모듈 docstring에 이미 "네트워크/Appium 의존 없음"이라고 명시된 순수
  구면삼각법 계산이다 — 기준 좌표(0km 지점)만 있으면 카카오 지오코딩
  없이 1.5~2.5km/2.5~3.5km 반경 지점을 그대로 계산할 수 있다. 카카오
  API(`crawler/geocode.py`)는 `STORE_ADDRESS`(주소 문자열)를 기준
  좌표(위도/경도) 하나로 바꾸는 데만 쓰인다 — 배민이 위도/경도를 이미
  직접 주므로 이 변환 자체가 필요 없어진다.
- 실제로 치밥대장 기준 정보로 `crawler/.env`를 바꿔서 크롤을 1회
  실행해봤다(2026-08-14~15): 0km 36위(광고 8개), 1.5~2.5km 64위(광고
  14개), 2.5~3.5km 25위(광고 6개) — 정상 동작 확인. 반경 구간은 매번
  랜덤 방위각 1개만 샘플링해서 변동폭이 크다는 것도 이 실측으로 확인
  됐다(범위 밖 절 참고).

## 스코프 결정: 치밥대장 하나만

`ad_campaigns.id=1`이 이미 `store_id=1`(대시보드 스토어 이름을 이번에
"치밥대장 숯불양념92치킨 노원당고개점"으로 실데이터 갱신함, 2026-08-14),
`category="치킨"`, `target_rank=3`으로 돼 있어 치밥대장과 자연스럽게
맞아떨어진다. 그래서 **새 캠페인을 만들지 않고 이 캠페인 하나에만
`shop_no`를 연결**한다. `ad_campaigns.id=2`(닭갈비연구소 당고점, 실제
배민 계정엔 대응하는 브랜드가 없음)는 이번 범위에서 완전히 안 건드리고
지금처럼 Mock 그대로 둔다.

## 데이터 모델 변경

`ad_campaigns`에 nullable 컬럼 추가:

```sql
ALTER TABLE ad_campaigns ADD COLUMN shop_no VARCHAR(20);
```

`shop_no`가 있는 캠페인만 아래 실측 로직을 타고, 없는 캠페인(지금의
닭갈비연구소)은 완전히 기존 Mock 경로 그대로 유지한다 — 캠페인 단위로
실데이터/Mock이 갈리는 구조라 이후 다른 브랜드를 추가하고 싶을 때도
같은 패턴(새 캠페인 만들고 `shop_no` 채우기)을 그대로 반복하면 된다.

일회성 수동 작업(로컬 검증 DB와 배포 DB 양쪽에 동일하게 적용):
```sql
UPDATE ad_campaigns SET shop_no = '14804318' WHERE id = 1;
```

## API 응답 매핑 — 새 스크레이퍼 함수

`GET /v4/store/shops/{shopNo}` 응답에서:

```
name              = 그대로 (STORE_DISPLAY_NAME으로 사용)
categories[0].name = 그대로 (CATEGORY_LABEL로 사용)
address.road.address = 그대로 (STORE_ADDRESS로 사용, 화면 표시용/수동
                        재현 참고용으로만 넘기고 실제 좌표 계산엔 안 씀)
address.latitude / address.longitude = 그대로 (크롤러 기준 좌표로 직접 사용)
```

## 동작 흐름

### 1. "우리가게 순위 확인" 버튼 → 실제 크롤 실행 (`_run_local_crawl`)

기존 흐름(`backend/app/routers/ads.py`의 `_run_local_crawl(campaign_id)`)은
로컬/원격 워커 두 경로 모두 결국 이 함수 하나로 모인다 — 그래서 이 함수
안에만 아래 단계를 추가하면 두 경로 모두에 자동으로 적용된다.

1. (신규) `campaign_id`로 `AdCampaign`을 조회해 `shop_no`를 확인한다.
2. `shop_no`가 없으면(지금의 닭갈비연구소) 기존 그대로 — `crawler/.env`
   설정을 그대로 쓴다(변경 없음).
3. `shop_no`가 있으면(치밥대장): 그 캠페인의 `store_id`로 배민
   `store_platform_connections`를 찾아 자격증명을 복호화 →
   `baemin_auth.login()`으로 로그인 → 새 함수
   `fetch_shop_info(page, shop_no)` 호출 → 상호명/카테고리/주소/위도/
   경도 확보 → 세션 종료.
4. 확보한 값을 크롤러 서브프로세스의 환경변수로 주입한다
   (`STORE_DISPLAY_NAME`, `CATEGORY_LABEL`, `STORE_ADDRESS`,
   `STORE_LAT`, `STORE_LNG`) — `crawler/.env` 파일은 안 건드린다.
5. 이 단계(로그인·정보 조회)가 실패하면(예: 자격증명 만료) **크롤
   자체를 하드 에러로 중단**한다 — `.env`로 조용히 폴백하지 않는다.
   폴백하면 엉뚱한 가게(지금 `.env`에 남아있는 값)를 실측한 결과가
   치밥대장 결과인 것처럼 저장될 위험이 있어서다.

### 2. `crawler/config.py` / `run_crawl.py` — 주입값 우선 사용

- `load_settings()`가 `.env` 파일 값보다 **프로세스 환경변수를 우선**
  하도록 바꾼다(표준적인 dotenv 우선순위 관례 — 이미 설정된 환경변수는
  `.env` 파일이 덮어쓰지 않는다). 개발자가 크롤러만 단독으로 테스트할
  땐(환경변수 없이 직접 실행) 지금처럼 `.env` 파일 값을 그대로 쓴다 —
  하위 호환 유지.
- `STORE_LAT`/`STORE_LNG`가 주어지면 `address_to_coords`(카카오 지오
  코딩) 호출을 건너뛰고 그 값을 기준 좌표로 바로 쓴다. 없으면(기존
  `.env`만 있는 경우) 지금처럼 `STORE_ADDRESS`를 카카오로 지오코딩한다
  — 카카오 의존성 자체는 안 지운다, 폴백 경로로 남긴다.

### 3. "광고 성과" 카드 — 치밥대장만 실데이터로

`GET /ads/performance`에서 `campaign.shop_no`가 있는 캠페인은
`ad_performance_metrics`(Mock) 대신 `BrandAdClickMetric`(이미 실데이터,
`shop_no`로 조회 가능)을 집계해서 `calculate_performance`에 넘긴다 —
`GET /ads/click-performance`가 이미 하는 것과 완전히 동일한 조회를
재사용한다. `shop_no`가 없는 캠페인은 기존 Mock 경로 그대로.

### 4. "순위 현황" 카드 — 치밥대장만 부분 실데이터로

`GET /ads/rank-monitoring`에서 `campaign.shop_no`가 있는 캠페인:
- `category`: 캠페인의 `category` 그대로(이미 실제 카테고리와 일치).
- `current_cpc`: `BrandAdClickMetric` 최근 14일 평균(광고비÷클릭수) —
  `calculate_performance`가 계산하는 것 재사용.
- `current_rank`/`rank_status`: `AdRankSnapshot`에서 이 캠페인의
  `distance_km = 0`(가게 주소 지점)인 가장 최근 실측 행을 가져온다 —
  "우리가게 순위 확인"을 한 번도 안 눌렀으면 `null`("아직 실측 데이터
  없음").
- `competitor_est_cpc`: **실측 불가능하므로 계속 추정치**로 남긴다 —
  기존 Mock 로직(목표 순위 대비 현재 순위로 추정)을 그대로 쓰되, 화면에
  "(추정)"이라고 명확히 표시해서 실측 항목과 섞이지 않게 한다(아래
  프론트 변경 참고).
- `recommended_action`: `current_rank`(실측) vs `target_rank` 비교
  기반의 단순 방향 판단(목표보다 순위가 밀리면 `raise_cpc`, 아니면
  `keep`)으로 재계산 — 구체적인 `suggested_cpc` 액수는 경쟁 CPC를 몰라
  정확히 계산할 수 없으므로 이번엔 안 준다(`null`).
- `shop_no`가 없는 캠페인(닭갈비연구소)은 기존 Mock 경로 완전히 그대로.

## 프론트 변경

`frontend/src/app/(app)/ads/page.tsx`:
- 응답에 추가되는 `is_estimate`류 플래그는 만들지 않는다 — 대신
  "경쟁 예상 CPC" 컬럼 헤더 자체를 "경쟁 예상 CPC (추정)"으로 바꿔서
  이 항목만 항상 추정치라는 걸 명시한다(다른 컬럼은 실측 가능한 캠페인
  기준으로 실측값이 나온다는 걸 페이지 상단 안내 문구로 설명).
- 페이지 상단 안내 문구를 지금의 "순위 현황·경쟁 CPC는 수집됐다고
  가정한 Mock 스냅샷입니다"에서, 치밥대장은 실측 기반이고 경쟁 CPC만
  추정이라는 걸 정확히 설명하는 문구로 갱신한다.

## 에러 처리

| 상황 | 처리 |
|---|---|
| `shop_no` 있는 캠페인의 가게 정보 조회(로그인/`fetch_shop_info`) 실패 | 크롤 작업 자체를 `error` 상태로 종료, 폴링 응답의 `error` 메시지에 원인 표시 — `.env` 폴백 없음 |
| `shop_no` 없는 캠페인 | 기존 동작 100% 그대로(변경 없음) |
| 실측 `AdRankSnapshot`이 아직 하나도 없는 상태에서 "순위 현황" 조회 | `current_rank: null`, `rank_status: null`, `recommended_action: "keep"` — 기존 캠페인에 스냅샷이 없을 때의 동작과 동일 |

## 테스트 계획

- **backend (pytest)**: `fetch_shop_info`(Playwright)는 이 저장소
  컨벤션대로 자동 테스트하지 않는다 — 이미 실 계정으로 4개 브랜드 전부
  라이브 검증했다(위 "조사 과정" 참고). `load_settings`의 환경변수
  우선순위 로직과 `run_crawl.py`의 `STORE_LAT`/`STORE_LNG` 분기는 순수
  로직이라 단위 테스트. `/ads/performance`, `/ads/rank-monitoring`의
  `shop_no` 유무에 따른 분기(실데이터 vs Mock)를 통합 테스트로 검증 —
  특히 `shop_no` 없는 캠페인(닭갈비연구소)이 이번 변경으로 전혀
  영향받지 않는지 회귀 테스트.
- **frontend**: `tsc --noEmit`. 로컬에서 실제로 "우리가게 순위 확인"을
  눌러서 치밥대장 정보로 크롤이 도는지(주소/상호명이 `.env` 값이 아니라
  실제 배민 값으로 바뀌는지), 순위 현황/광고 성과 카드가 실측으로
  바뀌는지, 닭갈비연구소는 그대로 Mock인지 직접 확인.

## CLAUDE.md 갱신

"창의 기능: 광고 순위 모니터링" 절 아래에, 반경별 실측(이미 예외 허용된
crawler/)에 이어 캠페인-브랜드 연결과 가게 정보 자동 조회도 실측
기반으로 바뀌었다는 내용을 추가한다. 치밥대장 하나만이고 나머지
브랜드/캠페인은 범위 밖이라는 점도 명시한다.

## 범위 밖

- 나머지 3개 실제 브랜드(블랙닭갈비/곱도리탕/행복가성비컵밥)와
  닭갈비연구소 당고점 캠페인 연결 — 필요해지면 같은 패턴(캠페인 추가 +
  `shop_no` 채우기)을 반복하는 별도 작업.
- "경쟁 예상 CPC" 실측 — 배민이 어떤 화면에서도 경쟁사 CPC를 노출하지
  않아 구조적으로 불가능. 계속 추정치.
- 반경 구간 샘플링 지점을 여러 개로 늘려 평균 내는 개선(지금은 구간당
  랜덤 방위각 1개라 변동폭이 큼, 사용자와 논의 중 나온 아이디어) — 크롤
  시간이 비례해서 늘어나는 트레이드오프가 있어 별도 논의 필요.
- 카카오 지오코딩 의존성 완전 제거 — 수동 `.env` 단독 실행 경로의
  하위 호환을 위해 폴백으로 남긴다.
