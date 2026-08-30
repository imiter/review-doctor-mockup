# 모바일 대시보드/리뷰/광고 UI·UX 리팩토링 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모바일 앱(ReviewDocterMobile)의 대시보드/리뷰/광고 화면에 원형 게이지·임계값 바를 도입하고, 우가클 브랜드 선택을 드롭다운 시트로, 리뷰 규칙/스타일을 카드로 바꾸고, 광고 캠페인에 실제 브랜드명을 표시한다(백엔드+웹도 브랜드명 필드는 같이 고침).

**Architecture:** `react-native-svg` 기반 순수 SVG 링 게이지 컴포넌트 1개, View 기반 임계값 바 컴포넌트 1개를 새로 만들고 기존 화면에 조합한다. 색상 임계값은 순수 함수로 분리해 jest로 단위 테스트한다. 브랜드명은 백엔드 `/ads/rank-monitoring`·`/ads/performance` 응답에 `display_name` optional 필드를 추가하는 방식(하위 호환 유지)으로 웹·앱이 공유한다.

**Tech Stack:** React Native 0.85.2, TypeScript, `react-native-svg`(신규), FastAPI/SQLAlchemy, pytest, jest.

## Global Constraints

- 팔레트는 `src/theme/colors.ts`(모바일) 값을 그대로 쓴다 — 새 색을 짓지 않는다.
- ACoS 점수 구간은 `backend/app/acos.py`의 `_score_from_acos`(10%/15%/25% 경계)와 정확히 같은 경계값을 프론트에서도 써야 한다.
- 기존 API 응답 필드는 절대 제거/이름변경하지 않는다 — `display_name`은 추가(optional)만 한다.
- 모바일 스크린/화면 전체 컴포넌트는 이 프로젝트에 jest 렌더 테스트 관례가 없으므로(기본 템플릿의 `__tests__/App.test.tsx` 하나뿐) 새로 도입하지 않는다 — 검증은 `npx tsc --noEmit` + iOS 시뮬레이터 + idb 스크린샷으로 한다(이 세션에서 이미 확립된 방식). 순수 로직(색상 임계값 함수, 백엔드 리졸버)만 실제 단위 테스트를 쓴다.
- `react-native-svg` 설치 후 반드시 `pod install` + 네이티브 리빌드(`react-native run-ios`)가 필요하다 — JS만 바꾸는 Fast Refresh로는 안 잡힌다.

---

## Task 1: 백엔드 — 광고 캠페인 브랜드명 리졸버 + 응답 필드

**Files:**
- Modify: `backend/app/routers/ads.py`
- Test: `backend/tests/test_ads.py`

**Interfaces:**
- Produces: `_resolve_campaign_display_name(db: Session, store_id: int, shop_no: str | None) -> str | None` — `shop_no`가 None이면 None, 매칭되는 `BaeminShopBrand`가 없어도 None(리뷰 답글 경로처럼 `store.name`으로 폴백하지 않는다 — 광고 캠페인은 브랜드명이 없으면 애초에 특정 매장이 아니라는 뜻이라 폴백이 의미 없다). `/ads/rank-monitoring`과 `/ads/performance` 각 응답 딕셔너리에 `"display_name"` 키 추가.

- [ ] **Step 1: 기존 테스트 파일 구조 확인**

Run: `cat backend/tests/test_ads.py | head -40`

기존 테스트가 `TestClient`와 어떤 fixture(`db_session`, `auth_headers` 등)를 쓰는지 확인한다 — 새 테스트도 같은 fixture를 재사용해야 한다.

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_ads.py` 끝에 추가:

```python
def test_rank_monitoring_includes_display_name_when_shop_no_matches(db_session, auth_headers, client):
    store = _make_store_with_baemin_connection(db_session)  # 기존 헬퍼 재사용, 없으면 아래 참고
    brand = BaeminShopBrand(
        connection_id=store["connection_id"],
        shop_no="12345",
        shop_name="[음식배달] 치밥대장 노원당고개점 / 치킨 99999",
    )
    db_session.add(brand)
    campaign = AdCampaign(store_id=store["store_id"], category="치킨", shop_no="12345", current_cpc=100, target_rank=3)
    db_session.add(campaign)
    db_session.commit()

    resp = client.get(f"/ads/rank-monitoring?store_id={store['store_id']}", headers=auth_headers)
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["campaign_id"] == campaign.id)
    assert row["display_name"] == "치밥대장 노원당고개점"


def test_rank_monitoring_display_name_null_when_no_shop_no(db_session, auth_headers, client):
    store = _make_store_with_baemin_connection(db_session)
    campaign = AdCampaign(store_id=store["store_id"], category="찜·탕·찌개", shop_no=None, current_cpc=100, target_rank=10)
    db_session.add(campaign)
    db_session.commit()

    resp = client.get(f"/ads/rank-monitoring?store_id={store['store_id']}", headers=auth_headers)
    row = next(r for r in resp.json() if r["campaign_id"] == campaign.id)
    assert row["display_name"] is None
```

`_make_store_with_baemin_connection`이 기존 파일에 없으면, 파일 상단의 다른 테스트가 store/connection을 어떻게 만드는지 그대로 따라 작은 헬퍼로 추출한다(패턴만 복사, 임의로 새 스키마 만들지 말 것).

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -k display_name -v`
Expected: FAIL — `KeyError: 'display_name'`

- [ ] **Step 4: 리졸버 구현**

`backend/app/routers/ads.py` 상단 import에 `re`와 `BaeminShopBrand`, `StorePlatformConnection`을 추가(이미 `StorePlatformConnection`은 import돼 있음 — `BaeminShopBrand`만 추가):

```python
from app.models import (
    AdCampaign,
    AdPerformanceMetric,
    AdRankSnapshot,
    BaeminShopBrand,
    BrandAdClickMetric,
    Order,
    Platform,
    Store,
    StorePlatformConnection,
    User,
)
```

`_latest_distance_points` 함수 위에 헬퍼 추가:

```python
def _resolve_campaign_display_name(db: Session, store_id: int, shop_no: str | None) -> str | None:
    """캠페인의 shop_no로 실제 배민 브랜드명을 찾는다. app/llm/generate.py의
    _resolve_display_name과 동일한 조회·정제 로직(태그/카테고리 번호 제거)을
    쓰되, 리뷰 답글과 달리 store.name으로 폴백하지 않는다 — shop_no가 없거나
    브랜드를 못 찾으면 이 캠페인은 애초에 특정 매장을 가리키지 않는다는
    뜻이라 대표 매장 이름을 대신 보여주면 오히려 오해를 준다."""
    if not shop_no:
        return None
    brand = db.scalar(
        select(BaeminShopBrand)
        .join(StorePlatformConnection, BaeminShopBrand.connection_id == StorePlatformConnection.id)
        .where(StorePlatformConnection.store_id == store_id, BaeminShopBrand.shop_no == shop_no)
    )
    if brand is None:
        return None
    name = re.sub(r"^\[[^\]]*\]\s*", "", brand.shop_name)
    return name.split(" / ")[0].strip() or None
```

`re` import를 파일 최상단(`import hmac` 근처)에 추가한다.

- [ ] **Step 5: `/ads/rank-monitoring`에 필드 연결**

`ads_rank_monitoring` 함수 안, `result.append({...})`가 두 곳(shop_no 있는 분기, 없는 분기) 있다 — 둘 다에 `"display_name": _resolve_campaign_display_name(db, sid, c.shop_no),` 한 줄씩 추가한다(shop_no 없는 분기는 항상 None이 나오지만, 필드 자체는 두 분기 모두 있어야 프론트가 분기 없이 읽을 수 있다).

- [ ] **Step 6: `/ads/performance`에 필드 연결**

`ads_performance` 함수의 `result.append({...})`에도 같은 줄 추가: `"display_name": _resolve_campaign_display_name(db, sid, c.shop_no),`

- [ ] **Step 7: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -k display_name -v`
Expected: PASS

- [ ] **Step 8: 전체 ads 테스트 회귀 확인**

Run: `cd backend && .venv/bin/pytest tests/test_ads.py -v`
Expected: 전부 PASS (기존 테스트가 응답 딕셔너리 키를 통째로 비교하는 방식이면 새 필드 때문에 깨질 수 있다 — 깨지면 그 assertion에 `display_name` 키를 추가해서 고친다)

- [ ] **Step 9: 로컬 백엔드 재시작 후 실제 응답 확인**

```bash
lsof -ti:8000 | xargs -r kill
cd backend && set -a && source .env && set +a && nohup .venv/bin/uvicorn app.main:app --reload --port 8000 > /tmp/review-docter-backend.log 2>&1 &
sleep 3
```

그 다음 `demo@dris.kr`로 로그인해 `/ads/rank-monitoring` 응답에 `display_name`이 "치킨" 캠페인만 채워져 있고 나머지 3개는 null인지 curl로 확인한다(이 세션에서 이미 여러 번 쓴 로그인+curl 패턴 그대로).

- [ ] **Step 10: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add backend/app/routers/ads.py backend/tests/test_ads.py
git commit -m "feat: 광고 캠페인 응답에 실제 브랜드명(display_name) 필드 추가"
```

---

## Task 2: 모바일 — 색상 임계값 순수 함수 + jest 테스트

**Files:**
- Create: `src/theme/scoreColor.ts`
- Test: `src/theme/scoreColor.test.ts`

**Interfaces:**
- Produces: `scoreColor(score: number): string`, `acosColor(acos: number): string` — 둘 다 `src/theme/colors.ts`의 `palette` 값을 반환한다. Task 4(RingGauge)와 Task 5(ThresholdBar)가 이 두 함수를 가져다 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`src/theme/scoreColor.test.ts` 새로 작성:

```typescript
import { scoreColor, acosColor } from './scoreColor';
import { palette } from './colors';

describe('scoreColor', () => {
  it('returns success for 90 and above', () => {
    expect(scoreColor(90)).toBe(palette.success);
    expect(scoreColor(100)).toBe(palette.success);
  });
  it('returns accent for 80-89', () => {
    expect(scoreColor(80)).toBe(palette.accent);
    expect(scoreColor(89)).toBe(palette.accent);
  });
  it('returns warning for 70-79', () => {
    expect(scoreColor(70)).toBe(palette.warning);
    expect(scoreColor(79)).toBe(palette.warning);
  });
  it('returns danger below 70', () => {
    expect(scoreColor(69)).toBe(palette.danger);
    expect(scoreColor(60)).toBe(palette.danger);
  });
});

describe('acosColor', () => {
  it('returns success below 10', () => {
    expect(acosColor(9.99)).toBe(palette.success);
  });
  it('returns accent for 10-14.99', () => {
    expect(acosColor(10)).toBe(palette.accent);
    expect(acosColor(14.99)).toBe(palette.accent);
  });
  it('returns warning for 15-24.99', () => {
    expect(acosColor(15)).toBe(palette.warning);
    expect(acosColor(24.99)).toBe(palette.warning);
  });
  it('returns danger at 25 and above', () => {
    expect(acosColor(25)).toBe(palette.danger);
    expect(acosColor(40)).toBe(palette.danger);
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile && npx jest src/theme/scoreColor.test.ts`
Expected: FAIL — `Cannot find module './scoreColor'`

- [ ] **Step 3: 구현**

`src/theme/scoreColor.ts` 새로 작성:

```typescript
import { palette } from './colors';

/**
 * backend/app/acos.py의 _score_from_acos와 동일한 경계값(90/80/70점).
 * 두 파일 중 하나만 바뀌면 UI 색상 구간과 실제 점수 산정 기준이
 * 어긋나므로, 경계값을 고칠 땐 반드시 양쪽을 같이 고친다.
 */
export function scoreColor(score: number): string {
  if (score >= 90) return palette.success;
  if (score >= 80) return palette.accent;
  if (score >= 70) return palette.warning;
  return palette.danger;
}

/**
 * backend/app/acos.py의 _score_from_acos 경계값(10%/15%/25%)과 동일.
 * ACoS는 낮을수록 좋다 — scoreColor와 방향이 반대다.
 */
export function acosColor(acos: number): string {
  if (acos < 10) return palette.success;
  if (acos < 15) return palette.accent;
  if (acos < 25) return palette.warning;
  return palette.danger;
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile && npx jest src/theme/scoreColor.test.ts`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```bash
cd /Users/kunhee/Developer/ReviewDocterMobile
git add src/theme/scoreColor.ts src/theme/scoreColor.test.ts
git commit -m "feat: 점수/ACoS 색상 임계값 순수 함수 추가"
```

---

## Task 3: 모바일 — `react-native-svg` 설치 + 네이티브 리빌드

**Files:**
- Modify: `package.json`, `ios/Podfile.lock`(자동 생성)

**Interfaces:**
- Produces: `react-native-svg`의 `Svg`, `Circle` export가 이후 Task 4에서 사용 가능해짐.

- [ ] **Step 1: 패키지 설치**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile && npm install react-native-svg`
Expected: `added 1 package`

- [ ] **Step 2: pod install**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile/ios && pod install`
Expected: `Pod installation complete!` — 의존성 개수가 기존보다 1개 늘어난다.

- [ ] **Step 3: Metro/시뮬레이터 프로세스 정리**

```bash
lsof -ti:8081 | xargs -r kill -9
lsof -ti:8082 | xargs -r kill -9
```

- [ ] **Step 4: 네이티브 리빌드**

```bash
cd /Users/kunhee/Developer/ReviewDocterMobile
nohup npx react-native run-ios --udid BFB845B0-A038-4B77-9BA0-CE1E02C1A27C > /tmp/rn-run-ios.log 2>&1 &
```

빌드는 몇 분 걸린다 — `tail -f /tmp/rn-run-ios.log`로 `success Successfully launched the app`가 뜰 때까지 기다린다(중간에 `- Building the app...` 반복은 정상).

- [ ] **Step 5: 앱 정상 기동 확인**

```bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
idb connect BFB845B0-A038-4B77-9BA0-CE1E02C1A27C
xcrun simctl io booted screenshot /tmp/svg_rebuild_check.png
```

스크린샷을 읽어 로그인 화면 또는 대시보드가 크래시 없이 뜨는지 확인한다(idb ui describe-all로 `ReviewDocterMobile` 루트 엘리먼트가 잡히면 정상).

- [ ] **Step 6: 커밋**

```bash
cd /Users/kunhee/Developer/ReviewDocterMobile
git add package.json package-lock.json ios/Podfile.lock
git commit -m "chore: react-native-svg 추가 (원형 게이지 컴포넌트용)"
```

---

## Task 4: 모바일 — RingGauge 컴포넌트

**Files:**
- Create: `src/components/RingGauge.tsx`

**Interfaces:**
- Consumes: `scoreColor` (Task 2), `palette` (`src/theme/colors.ts`), `Svg`/`Circle` (`react-native-svg`, Task 3)
- Produces: `RingGauge({ value, size, label, colorOverride }: RingGaugeProps)` 컴포넌트. `value`는 0~100, 범위 밖이면 클램프한다. `colorOverride`를 안 주면 `scoreColor(value)`를 쓴다(재주문율%처럼 "점수"가 아닌 값에 다른 색을 강제하고 싶을 때 override용).

- [ ] **Step 1: 컴포넌트 작성**

```tsx
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';

import { palette } from '../theme/colors';
import { scoreColor } from '../theme/scoreColor';

interface RingGaugeProps {
  value: number;
  size?: number;
  strokeWidth?: number;
  label?: string;
  colorOverride?: string;
  valueLabel?: string;
}

export default function RingGauge({
  value,
  size = 88,
  strokeWidth = 8,
  label,
  colorOverride,
  valueLabel,
}: RingGaugeProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (clamped / 100) * circumference;
  const color = colorOverride ?? scoreColor(clamped);
  const center = size / 2;

  return (
    <View style={{ width: size, height: size }}>
      <Svg width={size} height={size}>
        <Circle cx={center} cy={center} r={radius} stroke={palette.surface2} strokeWidth={strokeWidth} fill="none" />
        <Circle
          cx={center}
          cy={center}
          r={radius}
          stroke={color}
          strokeWidth={strokeWidth}
          fill="none"
          strokeDasharray={`${circumference} ${circumference}`}
          strokeDashoffset={offset}
          strokeLinecap="round"
          rotation="-90"
          origin={`${center}, ${center}`}
        />
      </Svg>
      <View style={styles.labelWrap} pointerEvents="none">
        <Text style={styles.value}>{valueLabel ?? Math.round(clamped)}</Text>
        {label && <Text style={styles.label}>{label}</Text>}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  labelWrap: { ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' },
  value: { color: palette.foreground, fontSize: 20, fontWeight: '700' },
  label: { color: palette.muted, fontSize: 9, marginTop: 1 },
});
```

- [ ] **Step 2: 타입체크**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile && npx tsc --noEmit`
Expected: `No errors found`

- [ ] **Step 3: 임시로 대시보드에 붙여서 육안 확인 (아직 최종 위치 아님)**

`DashboardScreen.tsx` 최상단 아무 곳에 `<RingGauge value={82} label="82점" />`를 임시로 추가하고 앱을 리로드(JS만 바뀐 거라 터미네이트+런치까지 필요 없고, 이미 Task 3에서 리빌드했으니 Fast Refresh로 충분)한 뒤 스크린샷으로 링이 82% 채워진 도넛으로 그려지는지 확인한다. 확인 후 이 임시 코드는 지운다(Task 6에서 진짜 위치에 다시 넣는다).

- [ ] **Step 4: 커밋**

```bash
cd /Users/kunhee/Developer/ReviewDocterMobile
git add src/components/RingGauge.tsx
git commit -m "feat: RingGauge 원형 게이지 컴포넌트 추가"
```

---

## Task 5: 모바일 — ThresholdBar 컴포넌트

**Files:**
- Create: `src/components/ThresholdBar.tsx`

**Interfaces:**
- Consumes: `palette` (`src/theme/colors.ts`)
- Produces: `ThresholdBar({ value, max }: ThresholdBarProps)` — `value`는 ACoS(%) 원값, `max`는 바가 표현하는 상한(기본 40 — acos.py의 마지막 경계 25%보다 넉넉히 위). 내부적으로 10/25 두 경계로 3구간(안전/주의/위험) 배경을 그리고 현재값 위치에 점을 찍는다.

- [ ] **Step 1: 컴포넌트 작성**

```tsx
import React from 'react';
import { StyleSheet, View } from 'react-native';

import { palette } from '../theme/colors';

interface ThresholdBarProps {
  value: number;
  max?: number;
}

const SAFE_BOUND = 10;
const WARNING_BOUND = 25;

export default function ThresholdBar({ value, max = 40 }: ThresholdBarProps) {
  const clamped = Math.max(0, Math.min(max, value));
  const pct = (clamped / max) * 100;
  const safeFlex = SAFE_BOUND;
  const warningFlex = WARNING_BOUND - SAFE_BOUND;
  const dangerFlex = max - WARNING_BOUND;

  return (
    <View style={styles.wrap}>
      <View style={styles.track}>
        <View style={[styles.band, { flex: safeFlex, backgroundColor: palette.success }]} />
        <View style={[styles.band, { flex: warningFlex, backgroundColor: palette.warning }]} />
        <View style={[styles.band, { flex: dangerFlex, backgroundColor: palette.danger }]} />
      </View>
      <View style={[styles.dot, { left: `${pct}%` }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { height: 12, justifyContent: 'center' },
  track: { flexDirection: 'row', height: 6, borderRadius: 3, overflow: 'hidden' },
  band: { height: '100%' },
  dot: {
    position: 'absolute',
    top: -1,
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: palette.foreground,
    borderWidth: 2,
    borderColor: palette.background,
    marginLeft: -6,
  },
});
```

- [ ] **Step 2: 타입체크**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile && npx tsc --noEmit`
Expected: `No errors found`

- [ ] **Step 3: 커밋**

```bash
cd /Users/kunhee/Developer/ReviewDocterMobile
git add src/components/ThresholdBar.tsx
git commit -m "feat: ThresholdBar 임계값 바 컴포넌트 추가"
```

---

## Task 6: 모바일 — 대시보드에 링 게이지 + 브랜드 드롭다운 시트 적용

**Files:**
- Modify: `src/features/dashboard/screens/DashboardScreen.tsx`

**Interfaces:**
- Consumes: `RingGauge`(Task 4), `ThresholdBar`(Task 5), `acosColor`(Task 2), 기존 `DetailModal`(`src/components/DetailModal.tsx`)

- [ ] **Step 1: import 추가**

파일 상단에 추가:

```typescript
import RingGauge from '../../../components/RingGauge';
import ThresholdBar from '../../../components/ThresholdBar';
```

- [ ] **Step 2: 브랜드 pill 나열 → 드롭다운 버튼으로 교체**

기존 `brandPickerRow`(브랜드 pill들을 가로로 나열하던 부분)를 지우고, 대신 상태 하나를 추가한다: `const [brandSheetOpen, setBrandSheetOpen] = useState(false);`

우가클 카드 헤더 부분을 아래로 교체한다(기존 `metricHeaderRow` 안의 `brandPickerRow` 조건부 렌더링을 통째로 이 버튼으로 바꾼다):

```tsx
{brands.length > 0 && (
  <Pressable
    style={styles.brandDropdownBtn}
    onPress={e => {
      e.stopPropagation();
      setBrandSheetOpen(true);
    }}
  >
    <Text style={styles.brandDropdownText} numberOfLines={1}>
      {brands.find(b => b.shop_no === activeShopNo)?.shop_name ?? '브랜드 선택'}
    </Text>
    <Text style={styles.brandDropdownChevron}>⌄</Text>
  </Pressable>
)}
```

- [ ] **Step 3: 브랜드 선택 시트 추가**

기존 4개 `DetailModal` 아래에 하나 더 추가:

```tsx
<DetailModal visible={brandSheetOpen} title="브랜드 선택" onClose={() => setBrandSheetOpen(false)}>
  {brands.map(b => (
    <Pressable
      key={b.shop_no}
      style={styles.brandOption}
      onPress={() => {
        setSelectedShopNo(b.shop_no);
        setBrandSheetOpen(false);
      }}
    >
      <Text style={[styles.brandOptionText, activeShopNo === b.shop_no && styles.brandOptionTextActive]}>
        {b.shop_name}
      </Text>
      {activeShopNo === b.shop_no && <Text style={styles.brandOptionCheck}>✓</Text>}
    </Pressable>
  ))}
</DetailModal>
```

- [ ] **Step 4: 우가클 점수 카드 본문을 RingGauge + ThresholdBar로 교체**

기존 `<Text style={styles.metricValue}>{clickPerfQuery.data?.score ?? '—'}점</Text>` + ACoS 텍스트 줄을 아래로 교체:

```tsx
<View style={styles.gaugeRow}>
  <RingGauge value={clickPerfQuery.data?.score ?? 0} size={72} strokeWidth={7} />
  <View style={{ flex: 1, marginLeft: 14 }}>
    <Text style={styles.metricSub}>ACoS {clickPerfQuery.data?.acos ?? '—'}%</Text>
    {clickPerfQuery.data?.acos != null && <ThresholdBar value={clickPerfQuery.data.acos} />}
  </View>
</View>
```

`clickPerfQuery.data?.score`가 없을 때 `RingGauge value={0}`을 쓰면 빈 링(0%)이 보인다 — "데이터 없음"과 "0점"을 구분하고 싶으면 `clickPerfQuery.data ? <RingGauge .../> : <Text style={styles.metricSub}>—</Text>`로 감싼다. 데이터 없는 매장(예: 방금 배민 연결한 신규 매장)이 실제로 있으므로 이 분기를 반드시 넣는다.

- [ ] **Step 5: 재주문율 카드도 RingGauge로 교체**

재주문율 카드의 `<Text style={styles.metricValue}>{...percent(data.repurchase_rate_adjusted)...}</Text>`를:

```tsx
{data.repurchase_rate_adjusted !== null ? (
  <RingGauge
    value={data.repurchase_rate_adjusted * 100}
    size={72}
    strokeWidth={7}
    colorOverride={palette.accent}
    valueLabel={`${(data.repurchase_rate_adjusted * 100).toFixed(1)}%`}
  />
) : (
  <Text style={styles.metricValue}>—</Text>
)}
```

재주문율은 acos.py 점수 구간과 무관한 지표라 `colorOverride={palette.accent}`로 항상 같은 색을 쓴다(scoreColor의 90/80/70 경계가 재주문율에는 안 맞는다 — 25%가 나쁜 게 아니므로).

- [ ] **Step 6: 스타일 추가**

`StyleSheet.create` 안에 추가:

```typescript
brandDropdownBtn: {
  flexDirection: 'row',
  alignItems: 'center',
  gap: 4,
  maxWidth: 160,
  paddingHorizontal: 10,
  paddingVertical: 5,
  borderRadius: 8,
  borderWidth: 1,
  borderColor: palette.border,
  backgroundColor: palette.surface2,
},
brandDropdownText: { color: palette.foreground, fontSize: 11.5, fontWeight: '600', flexShrink: 1 },
brandDropdownChevron: { color: palette.muted, fontSize: 11 },
brandOption: {
  flexDirection: 'row',
  justifyContent: 'space-between',
  alignItems: 'center',
  paddingVertical: 14,
  borderBottomWidth: 1,
  borderBottomColor: palette.border,
},
brandOptionText: { color: palette.foreground, fontSize: 14 },
brandOptionTextActive: { color: palette.accent, fontWeight: '600' },
brandOptionCheck: { color: palette.accent, fontSize: 16, fontWeight: '700' },
gaugeRow: { flexDirection: 'row', alignItems: 'center' },
```

기존에 있던 `brandPickerRow`/`brandPill`/`brandPillActive`/`brandPillText`/`brandPillTextActive` 스타일 정의는 이제 안 쓰이므로 지운다.

- [ ] **Step 7: 타입체크**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile && npx tsc --noEmit`
Expected: `No errors found`

- [ ] **Step 8: 시뮬레이터 검증**

앱은 이미 떠 있고 Fast Refresh로 잡히므로 리빌드는 필요 없다. 대시보드 탭으로 이동 → 스크린샷 → 다음을 눈으로 확인:
1. 우가클 점수가 링 게이지로 보이는지
2. 카드 헤더의 브랜드 표시가 pill 나열이 아니라 버튼 하나 + 셰브론인지
3. 그 버튼을 탭하면 브랜드 목록 시트가 열리고, 다른 브랜드를 선택하면 시트가 닫히며 점수가 바뀌는지
4. 재주문율 카드도 링 게이지로 보이는지

(이 세션에서 이미 쓴 idb 좌표 측정 방식 — `idb ui describe-all`로 정확한 좌표를 구한 뒤 `idb ui tap`, 스크린샷은 `xcrun simctl io booted screenshot`)

- [ ] **Step 9: 커밋**

```bash
cd /Users/kunhee/Developer/ReviewDocterMobile
git add src/features/dashboard/screens/DashboardScreen.tsx
git commit -m "feat: 대시보드 우가클/재주문율에 링 게이지 적용, 브랜드 선택 드롭다운화"
```

---

## Task 7: 모바일 — 광고 탭 성과 그리드에 링 게이지/임계값 바 + 브랜드명 표시

**Files:**
- Modify: `src/features/ads/adsApi.ts`
- Modify: `src/features/ads/screens/AdsScreen.tsx`

**Interfaces:**
- Consumes: `RingGauge`(Task 4), `ThresholdBar`(Task 5), 백엔드 `display_name` 필드(Task 1)

- [ ] **Step 1: 타입에 `display_name` 추가**

`adsApi.ts`의 `RankMonitoringItem`과 `AdPerformanceItem` 인터페이스 둘 다에 `display_name: string | null;` 필드를 추가한다.

- [ ] **Step 2: 순위 현황 카드 제목에 브랜드명 반영**

`AdsScreen.tsx`의 `rankQuery.data?.map(item => ...)` 블록 안, `<Text style={styles.category}>{item.category}</Text>`를:

```tsx
<Text style={styles.category}>
  {item.display_name ? `${item.display_name} · ${item.category}` : item.category}
</Text>
```

- [ ] **Step 3: 반경별 실측 순위 카드 제목도 동일하게**

`distanceQuery.data?.map(c => ...)` 블록 안 `<Text style={styles.category}>{c.category}</Text>`도 Step 2와 같은 패턴으로 바꾼다. 단 `DistanceRankRow` 타입에는 `display_name`이 없으므로, `rankQuery.data`에서 같은 `campaign_id`를 찾아 매칭한다: 이미 이 블록 위에 `const rank = rankQuery.data?.find(r => r.campaign_id === c.campaign_id);`가 있으므로 `rank?.display_name`을 쓴다.

```tsx
<Text style={styles.category}>
  {rank?.display_name ? `${rank.display_name} · ${c.category}` : c.category}
</Text>
```

- [ ] **Step 4: import 추가**

```typescript
import RingGauge from '../../../components/RingGauge';
import ThresholdBar from '../../../components/ThresholdBar';
```

- [ ] **Step 5: 성과 그리드에 링 게이지/임계값 바 적용**

`perfQuery.data.map(item => ...)` 블록의 `<View style={styles.perfGrid}>...</View>` + 아래 `scoreRow`를 아래로 교체:

```tsx
<View style={styles.perfGrid}>
  <PerfStat label="광고비" value={won(item.ad_spend)} />
  <PerfStat label="클릭수" value={`${item.clicks}회`} />
  <PerfStat label="광고주문" value={`${item.ad_orders}건`} />
  <PerfStat label="광고매출" value={won(item.ad_revenue)} />
  <PerfStat label="CPC" value={won(Math.round(item.cpc))} />
  <PerfStat label="AOV" value={won(Math.round(item.aov))} />
</View>
<View style={styles.perfBottomRow}>
  <View style={{ flex: 1 }}>
    <Text style={styles.perfLabel}>CVR</Text>
    <Text style={styles.perfValue}>{percent(item.cvr)}</Text>
    <Text style={[styles.perfLabel, { marginTop: 10 }]}>ACoS</Text>
    {item.acos !== null ? <ThresholdBar value={item.acos} /> : <Text style={styles.perfValue}>—</Text>}
  </View>
  <RingGauge value={item.score ?? 0} size={64} strokeWidth={6} />
</View>
```

`CVR`을 성과 그리드에서 빼서 아래 줄로 옮긴 이유는 ACoS 임계값 바가 가로로 넓은 공간이 필요해서다 — 6칸 그리드(광고비/클릭수/광고주문/광고매출/CPC/AOV)로 줄이고 CVR·ACoS·점수는 별도 줄에 배치한다.

- [ ] **Step 6: 스타일 추가/정리**

`scoreRow`/`scoreLabel`/`scoreValue` 스타일은 이제 안 쓰이므로 지우고, 대신 추가:

```typescript
perfBottomRow: {
  flexDirection: 'row',
  alignItems: 'center',
  marginTop: 14,
  paddingTop: 14,
  borderTopWidth: 1,
  borderTopColor: palette.border,
},
```

- [ ] **Step 7: 타입체크**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile && npx tsc --noEmit`
Expected: `No errors found`

- [ ] **Step 8: 시뮬레이터 검증**

광고 탭으로 이동 → 스크린샷으로 다음 확인:
1. "치킨" 캠페인만 "치밥대장 노원당고개점 · 치킨"처럼 브랜드명이 붙고, 나머지 3개(찜·탕·찌개/백반·죽·국수/고기·구이)는 카테고리만 있는지
2. 성과 그리드 하단에 ACoS 임계값 바 + 성과 점수 링 게이지가 보이는지
3. ACoS가 null인 카드(이전에 크래시 났던 그 카드)가 여전히 크래시 없이 "—"로 뜨는지

- [ ] **Step 9: 커밋**

```bash
cd /Users/kunhee/Developer/ReviewDocterMobile
git add src/features/ads/adsApi.ts src/features/ads/screens/AdsScreen.tsx
git commit -m "feat: 광고 탭에 브랜드명 표시 및 성과 지표 시각화(링 게이지/임계값 바) 적용"
```

---

## Task 8: 모바일 — 리뷰 관리 화면 답글 설정 카드화

**Files:**
- Modify: `src/features/reviews/screens/ReviewsScreen.tsx`

- [ ] **Step 1: 현재 헤더 구조 확인**

Run: `cat src/features/reviews/screens/ReviewsScreen.tsx | head -60`

우측 상단 "규칙"/"스타일" 텍스트 링크가 어떤 헤더 컴포넌트/스타일로 돼 있는지, `navigation.navigate('ReplyRules')`/`navigation.navigate('ReplyStyles')` 호출부를 정확히 찾는다.

- [ ] **Step 2: 상단 텍스트 링크 제거, 카드 섹션 추가**

우측 상단 링크 2개(`headerAction` 스타일 관련 `Pressable`/`Text`)를 지운다. 대신 필터 pill(미답변/전체) 아래, 리뷰 목록(`ListView`/`.map` 시작) 위에 아래 블록을 추가:

```tsx
<View style={styles.settingsRow}>
  <Pressable style={styles.settingsCard} onPress={() => navigation.navigate('ReplyRules')}>
    <Text style={styles.settingsIcon}>⚙️</Text>
    <Text style={styles.settingsTitle}>답글 규칙</Text>
    <Text style={styles.settingsDesc}>자동 제출 조건과 별점 규칙</Text>
  </Pressable>
  <Pressable style={styles.settingsCard} onPress={() => navigation.navigate('ReplyStyles')}>
    <Text style={styles.settingsIcon}>🎨</Text>
    <Text style={styles.settingsTitle}>답글 스타일</Text>
    <Text style={styles.settingsDesc}>말투와 페르소나</Text>
  </Pressable>
</View>
```

- [ ] **Step 3: 스타일 추가**

```typescript
settingsRow: { flexDirection: 'row', gap: 10, marginBottom: 16 },
settingsCard: {
  flex: 1,
  backgroundColor: palette.surface,
  borderWidth: 1,
  borderColor: palette.border,
  borderRadius: 12,
  padding: 12,
},
settingsIcon: { fontSize: 18, marginBottom: 6 },
settingsTitle: { color: palette.foreground, fontSize: 13, fontWeight: '600', marginBottom: 2 },
settingsDesc: { color: palette.muted, fontSize: 10.5, lineHeight: 14 },
```

기존 `headerAction` 관련 스타일(이제 안 쓰임)은 지운다.

- [ ] **Step 4: 타입체크**

Run: `cd /Users/kunhee/Developer/ReviewDocterMobile && npx tsc --noEmit`
Expected: `No errors found`

- [ ] **Step 5: 시뮬레이터 검증**

리뷰 탭으로 이동 → 스크린샷으로 "답글 규칙"/"답글 스타일" 카드 2개가 필터 pill 아래·리뷰 목록 위에 아이콘+제목+설명 형태로 보이는지, 탭하면 각각 규칙/스타일 화면으로 이동하는지 확인.

- [ ] **Step 6: 커밋**

```bash
cd /Users/kunhee/Developer/ReviewDocterMobile
git add src/features/reviews/screens/ReviewsScreen.tsx
git commit -m "feat: 리뷰 관리 화면 답글 규칙/스타일을 카드로 노출(발견성 개선)"
```

---

## Task 9: 웹 — 광고 카테고리에 브랜드명 반영 (백엔드 필드 재사용)

**Files:**
- Modify: `frontend/src/app/(app)/ads/page.tsx`

**Interfaces:**
- Consumes: Task 1에서 이미 배포된 백엔드 `display_name` 필드 (프론트 재배포만 필요, 백엔드는 이미 끝남)

- [ ] **Step 1: 타입에 필드 추가**

`RankRow`와 `DistanceRankRow` 타입 정의에 `display_name: string | null;` 추가.

- [ ] **Step 2: 순위 현황 테이블 셀 수정**

`<td className="py-3">{r.category}</td>`를:

```tsx
<td className="py-3">{r.display_name ? `${r.display_name} · ${r.category}` : r.category}</td>
```

- [ ] **Step 3: 반경별 실측 순위 카드 제목 수정**

`<p className="text-sm font-medium">{c.category}</p>`를 (모바일 Task 7-Step3와 동일하게 `ranks.find`로 매칭된 `rank.display_name` 사용):

```tsx
<p className="text-sm font-medium">
  {rank?.display_name ? `${rank.display_name} · ${c.category}` : c.category}
</p>
```

(이미 이 블록 위에 `const rank = ranks.find((r) => r.campaign_id === c.campaign_id);`가 있다.)

- [ ] **Step 4: 로컬 확인**

Run: `cd frontend && npm run dev` (이미 떠 있으면 생략) 후 브라우저에서 `demo@dris.kr`로 로그인해 `/ads` 페이지의 "치킨" 행에만 브랜드명이 붙는지 확인.

- [ ] **Step 5: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add frontend/src/app/\(app\)/ads/page.tsx
git commit -m "feat: 웹 광고 순위 화면에도 실제 브랜드명 표시"
```

---

## 최종 점검

- [ ] `cd /Users/kunhee/Developer/ReviewDocterMobile && npx tsc --noEmit` — 전체 클린
- [ ] `cd /Users/kunhee/Developer/ReviewDocterMobile && npx jest` — scoreColor 테스트 포함 전부 PASS
- [ ] `cd /Users/kunhee/Developer/review-docter/backend && .venv/bin/pytest tests/test_ads.py -v` — 전부 PASS
- [ ] 시뮬레이터에서 `demo@dris.kr`로 로그인해 대시보드/리뷰/광고 탭을 순서대로 스크린샷 검증
