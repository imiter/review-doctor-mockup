# 배민 리뷰 실데이터 연동 — 설계

날짜: 2026-08-09
관련 결정: CLAUDE.md "방향 전환" 로드맵 3번("실제 배달 플랫폼 데이터 연동")의 첫 단계.
배민만, 리뷰만 먼저 한다. 주문/정산 실데이터 연동과 쿠팡이츠/요기요는 이번 범위 밖.

## 배경 / 목적

지금까지 리뷰는 전부 `seed.sql`의 Mock 데이터였다. "가게 연결" 화면도 배민 포함
전 플랫폼이 Mock 로그인 모달(입력한 ID/PW를 서버로 보내지도 않는 UI 전용
연출)이었다. 이제 배민에 한해 실제로 로그인해서 실제 리뷰를 우리 DB에
적재한다.

## 조사 과정에서 확인된 사실 (중요)

실제 계정으로 크롬 개발자도구 Network 탭을 직접 확인해 다음을 검증했다:

- 사장님광장(self.baemin.com)은 React SPA이고, 화면이 부르는 내부 API가
  `self-api.baemin.com`에 별도로 있다. 리뷰 목록 API:
  `GET /v1/review/shops/{shopNo}/reviews?from=YYYY-MM-DD&to=YYYY-MM-DD&offset=N&limit=N`
  — 실제 응답을 받아 필드를 전부 확인했다(아래 "리뷰 API 응답 매핑" 참고).
- 주문내역 API도 별도로 존재하고(정확한 URL은 구현 시 재확인) 주문번호·금액·
  시각·메뉴가 실제 값으로 나온다. 다만 **리뷰 API와 주문내역 API 어느 쪽에도
  서로를 연결하는 공통 키가 없다** — 리뷰엔 "리뷰번호"(`id`)와 고객
  번호(`memberNo`)만 있고, 주문내역엔 "주문번호"(`orderNumber`)만 있다.
  주문 목록의 "⌄" 펼치기도 이미 받아온 같은 응답 안의 `items[].options`를
  펼쳐 보여줄 뿐 별도 API를 호출하지 않는다 — 즉 화면 어디에도 리뷰↔주문
  연결 정보가 없다.
- 이 조사를 바탕으로 **리뷰와 주문/정산을 DB에서 억지로 연결하지 않기로
  결정했다.** 세일즈랩류 서비스나 배민 API 재판매 업체(hyphen.im 등, 마케팅
  페이지에 "스크래핑 방식"이라고 명시돼 있음)도 정산/매출은 배민이 제공하는
  별도 목록에서 그대로 가져오고, 리뷰는 리뷰대로 독립적으로 다루는 것으로
  보인다. 우리도 같은 방식으로 간다 — 이번 범위는 리뷰만, 주문/정산 실데이터
  연동은 완전히 별도의 다음 단계로 미룬다.

## 데이터 모델 변경

### `reviews` 테이블

기존에 `reviews.order_id`가 `NOT NULL UNIQUE REFERENCES orders(id)`였고,
매장/플랫폼/메뉴 정보를 전부 `review.order`를 거쳐 조회했다(리뷰 목록 조회,
답글 생성/등록 권한 체크, 답글 템플릿의 매장명·메뉴명까지 전부). 리뷰를
주문 없이 독립적으로 넣으려면 이 의존을 끊어야 한다.

- `order_id`: **nullable로 변경** (있으면 좋고 없어도 되는 선택적 링크로 강등.
  이번 범위에서 실제로 채워지는 경우는 없다 — 주문 연동이 없으므로 — 이지만
  스키마는 미래를 위해 nullable FK로 남겨둔다)
- `store_id BIGINT NOT NULL REFERENCES stores(id) ON DELETE CASCADE` — 신규.
  리뷰가 자기 매장을 직접 안다.
- `platform_id INT NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT` — 신규.
  리뷰가 자기 플랫폼을 직접 안다.
- `menu_summary VARCHAR(200) NOT NULL` — 신규. 주문 조인 없이 메뉴명을 직접
  저장(배민 리뷰 API의 `menus[].name`을 그대로 씀).
- `external_review_id BIGINT UNIQUE` — 신규, nullable. 배민 리뷰 API의 `id`
  필드(예: `2026080402827696`). 재동기화 시 이미 넣은 리뷰인지 판별하는 키.
  Mock 리뷰는 NULL.

기존 Mock 리뷰(`seed.sql`)는 만들 때 이미 매장/플랫폼/메뉴를 알고 있으므로
그대로 채워 넣으면 된다 — 별도 백필 마이그레이션이 필요 없다(로컬은
`schema.sql`+`seed.sql` 재실행이 곧 "마이그레이션"이므로).

`backend/app/routers/reviews.py`의 모든 엔드포인트가 `review.order.store`,
`review.order.platform`, `review.order.menu_summary`를 쓰던 것을
`review.store`, `review.platform`, `review.menu_summary`로 바꿔야 한다
(목록 조회의 INNER JOIN도 `Order` 대신 `Review.store_id == sid`로 직접
필터링). API 응답 필드 이름은 그대로 유지되므로 프론트엔드는 변경 없음.

### `store_platform_connections` 테이블

- `credential_ciphertext TEXT` — 신규, nullable. 배민 ID/PW를 암호화해서
  JSON(`{"login_id": "...", "password": "..."}`)으로 저장. 다른 플랫폼(Mock)은
  계속 NULL.

### 새 테이블 `review_sync_jobs`

동기화 작업 상태 추적용.

```sql
CREATE TABLE review_sync_jobs (
    id             BIGSERIAL PRIMARY KEY,
    store_id       BIGINT      NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    platform_id    INT         NOT NULL REFERENCES platforms(id) ON DELETE RESTRICT,
    status         VARCHAR(10) NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'success', 'failed')),
    reviews_fetched  INT,
    reviews_inserted INT,
    error_message    TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ
);
```

## 자격증명 암호화

`backend/app/credential_crypto.py` 신규 — `cryptography` 패키지의 Fernet
대칭키 암호화. `CREDENTIAL_ENCRYPTION_KEY` 환경변수(새 Fernet 키, 배포 시
1회 생성해서 Railway에 등록)로 암호화/복호화. 원문 ID/PW는 로그 어디에도
남기지 않는다(카카오 시크릿·Resend 키와 동일한 "환경변수로만" 원칙).

## 크롤러 아키텍처

`backend/scrapers/` 신규 디렉토리(백엔드 프로세스 안에서 직접 import해
실행 — 실기기 자동화가 필요한 `crawler/`와 달리 헤드리스 브라우저라 같은
컨테이너에서 충분히 돌아간다):

- `backend/scrapers/baemin_auth.py` — Playwright로 사장님광장에 실제
  로그인해서 인증된 세션(쿠키/컨텍스트)을 반환. 로그인 폼의 정확한
  선택자(input 이름, 버튼 등)는 스크립트가 추측하지 않고 구현 시점에 실제
  로그인 화면을 열어 확인한다(`crawler/`의 기존 원칙과 동일). 이 세션으로
  로그인 직후 계정에 연결된 매장(shopNo) 정보도 함께 확인한다 — 한 계정에
  여러 매장이 있을 경우 첫 번째 매장을 사용하고 매장 선택 UI는 만들지
  않는다(CLAUDE.md의 "복잡한 권한/다중 사업자 권한 관리 금지" 원칙과 일치,
  범위 밖).
- `backend/scrapers/baemin_reviews.py` — 인증된 세션으로
  `self-api.baemin.com`의 리뷰 API를 **HTML 파싱이 아니라 직접 HTTP
  호출**로 두드린다(Playwright의 request 컨텍스트가 로그인 때 받은 쿠키를
  그대로 들고 있어 추가 인증 없이 호출 가능). `offset`/`limit`으로
  페이지네이션하며 `next: false`가 나올 때까지 반복.

### 리뷰 API 응답 매핑

실제 확인된 응답 필드 → 우리 스키마:

| 배민 API 필드 | 우리 컬럼 |
|---|---|
| `id` | `external_review_id` |
| `rating` (float, 5.0) | `rating` (SMALLINT로 반올림/캐스팅) |
| `contents` | `content` (빈 문자열 가능) |
| `memberNickname` | `customer_nickname` |
| `orderCount` | `customer_order_count` |
| `menus[0].name` | `menu_summary` (메뉴가 여러 개면 첫 번째 + "외 N건" 표기, 회원가입 위자드의 기존 패턴과 동일하게 처리) |
| `createdAt` | `created_at` |
| (요청 시 알고 있는 store_id/platform_id) | `store_id`, `platform_id` |
| — | `status = 'unanswered'` (신규 삽입 시 기본값, 이미 있으면 갱신하지 않음) |

`displayStatus`가 `DISPLAY`가 아닌 리뷰(차단/숨김)는 가져오지 않는다.

## 실행 흐름

1. **"가게 연결" 화면에서 배민 카드 클릭** → 기존 Mock 로그인 모달과 달리
   실제 ID/PW를 받는 전용 흐름으로 분기(다른 플랫폼은 기존 Mock 그대로 유지).
2. `POST /store-connections/baemin/login {platform_login_id,
   platform_login_password}` — Playwright로 실제 테스트 로그인.
   - 성공: 자격증명 암호화 저장 + `StorePlatformConnection` 생성
     (`platform_store_id`에 실제 shopNo 저장 — 더 이상 Mock `MK-XXXXXXXX`가
     아니라 진짜 값), 응답으로 매장명 등 확인 정보 반환.
   - 실패(아이디/비번 오류 추정): 로그인 페이지의 에러 메시지를 최대한
     그대로 사용자에게 보여준다.
   - 실패(캡차/이상 로그인 탐지 등, 사전에 확신 불가): 일반적인 "로그인에
     실패했습니다. 잠시 후 다시 시도해주세요" 메시지. 이 경우는 사전에
     완전히 막을 방법이 없다는 걸 알려진 제약으로 남긴다.
3. 연결되면 "가게 연결" 화면에 "리뷰 동기화" 버튼이 나타난다.
4. `POST /store-connections/baemin/sync-reviews` — `review_sync_jobs` 행을
   `pending`으로 생성하고 FastAPI `BackgroundTasks`로 실제 작업을 큐잉,
   job id를 즉시 응답.
5. 백그라운드 작업: 저장된 자격증명 복호화 → 로그인 → 리뷰 목록 페이지네이션
   전체 수집 → `external_review_id` 기준으로 이미 있는 리뷰는 건너뛰고 새
   리뷰만 삽입 → `review_sync_jobs`를 `success`(수집/삽입 개수 포함) 또는
   `failed`(에러 메시지 포함)로 갱신.
6. `GET /store-connections/baemin/sync-status/{job_id}` — 프론트가 3~5초
   간격으로 폴링. 완료되면 "42개 중 40개 신규 추가"처럼 결과를 보여준다.

## 에러 처리 요약

| 상황 | 처리 |
|---|---|
| 로그인 실패(아이디/비번) | 배민 에러 메시지 최대한 그대로 표시 |
| 로그인 실패(캡차/봇 탐지, 원인 불명) | 일반 실패 메시지, 알려진 제약으로 문서화 |
| 동기화 중 세션 만료 | job을 `failed`로 기록, 사용자에게 재연결 안내 |
| 이미 동기화한 리뷰 재수집 | `external_review_id`로 중복 스킵(정상 동작, 에러 아님) |
| 한 계정에 매장 여러 개 | 첫 번째 매장 사용, 선택 UI 없음(범위 밖) |

## 배포 고려사항

Railway 컨테이너에 Playwright 브라우저 바이너리 설치가 빌드 단계에 추가로
필요하다(`playwright install chromium --with-deps` 또는 이에 준하는 빌드
스텝). `backend/railway.json`의 빌드 설정에 반영해야 한다 — 구현 태스크에서
직접 처리.

## 테스트 계획

- **backend (pytest)**: 리뷰 API 응답 매핑 함수(순수 함수로 분리) 단위
  테스트 — 실제로 받은 위 예시 JSON을 고정 fixture로 써서 필드 매핑을
  검증. `review_sync_jobs` 상태 전이, `external_review_id` 기준 중복 스킵,
  `reviews.store_id`/`platform_id`/`menu_summary` 기반으로 바뀐
  `list_reviews`/답글 엔드포인트들의 권한 체크·조회가 여전히 올바르게
  동작하는지(기존 Mock 리뷰 기준 회귀 테스트). Playwright 로그인 자체는
  네트워크 요청을 모킹하지 않고 실제로 검증하기 어려우므로, 로그인 함수는
  얇게 분리해 그 아래(API 호출·매핑·DB 적재) 로직만 자동 테스트로
  촘촘히 덮는다.
- **frontend**: `tsc --noEmit`. 로컬에서 실제 계정으로 "가게 연결" → 로그인
  → "리뷰 동기화" → 실제 리뷰가 리뷰 관리 화면에 뜨는지까지 직접 확인.

## CLAUDE.md 갱신

"절대 금지" 목록의 "실제 배민/쿠팡이츠/요기요 등 플랫폼 API 연동 금지"에서
배민을 빼고 "쿠팡이츠/요기요 API 연동 금지"로 좁힌다. "실제 리뷰 크롤링
금지"도 배민에 한해 예외 허용으로 바꾼다. 카카오 로그인·이메일 인증과 같은
형식으로 "### 배민 리뷰 연동 (예외 허용)" 절을 추가해 이 결정과 근거(위
"조사 과정에서 확인된 사실")를 기록한다.

## 범위 밖

- 배민 주문내역/정산 실데이터 연동 (완전히 별도 다음 단계).
- 쿠팡이츠/요기요 실데이터 연동.
- 리뷰 답글 실제 자동 등록(여전히 절대 금지 — 답글 생성은 Mock 템플릿,
  등록은 사장님이 배민 앱에서 직접).
- 한 배민 계정의 다중 매장 선택 UI.
- 캡차/이상 로그인 탐지 우회(발생 시 실패로 처리하고 알려진 제약으로 남김).
