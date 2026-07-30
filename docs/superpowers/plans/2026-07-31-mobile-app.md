# React Native 모바일 앱 (iOS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹(review-docter)과 같은 FastAPI 백엔드를 쓰는 iOS용 Expo/React Native 앱을 만든다. 핵심 화면 4개(대시보드/리뷰 관리/광고 순위 모니터링/매출)를 웹과 같은 다크 테마로 구현한다.

**Architecture:** `mobile/` (저장소 루트, `backend/`·`frontend/`·`crawler/`와 형제 디렉토리)에 Expo managed workflow 앱. 로그인 스택 + 하단 탭 4개(React Navigation). API 클라이언트는 웹 `frontend/src/lib/api.ts` 패턴을 그대로 이식하되 토큰 저장소만 `expo-secure-store`로 교체. 새 백엔드 엔드포인트는 만들지 않고 기존 웹이 쓰는 API를 그대로 재사용한다.

**Tech Stack:** Expo (managed), React Native, TypeScript, React Navigation (native-stack 불필요, bottom-tabs만), expo-secure-store, @expo/vector-icons.

## Global Constraints

- 스펙 문서: `docs/superpowers/specs/2026-07-31-mobile-app-design.md`. 이 계획이 스펙과 다르면 스펙이 우선.
- 화면은 정확히 4개만: 대시보드, 리뷰 관리, 광고 순위 모니터링, 매출. 웹의 다른 5개 화면은 만들지 않는다.
- "우리가게 순위 확인" 실행 버튼은 앱에 넣지 않는다 — 광고 순위 모니터링은 조회 전용.
- 매장 전환 UI를 만들지 않는다. 모든 API 호출에 `store_id`를 넣지 않고, 백엔드가 사장님의 첫 매장으로 기본 처리하게 둔다.
- 새 백엔드 엔드포인트를 추가하지 않는다. 기존 라우터(`backend/app/routers/*.py`)의 응답 필드명을 정확히 그대로 쓴다.
- API 베이스 URL은 `http://localhost:8000` 고정 (iOS 시뮬레이터는 맥의 localhost를 공유).
- 색상 토큰은 웹 `frontend/src/app/globals.css`와 정확히 동일한 값을 쓴다: background `#0b0e14`, surface `#12161f`, surface2 `#171c27`, borderSubtle `#232935`, foreground `#e7e9ee`, muted `#8b93a7`, accent `#6d5ef5`, accentSoft `#6d5ef526`, success `#34d399`, warning `#fbbf24`, danger `#f87171`, dangerSoft `#f8717126`.
- 폰트는 iOS 시스템 폰트(San Francisco)를 그대로 쓴다 — Geist를 번들링하지 않는다.
- **테스트 방식**: 이 프로젝트는 Jest/Detox 등 자동화 UI 테스트를 쓰지 않기로 스펙에서 이미 결정했다(교육 과제물 성격에 과함). 각 태스크의 검증은 `npx tsc --noEmit`(타입 정확성)로 하고, 화면들이 실제로 서로 잘 붙어 동작하는지는 마지막 통합 태스크에서 iOS 시뮬레이터로 한 번에 확인한다.
- 로컬 백엔드가 `http://localhost:8000`에서 실행 중이어야 각 화면 검증이 가능하다 (`cd backend && .venv/bin/uvicorn app.main:app --reload`).

---

### Task 1: Expo 프로젝트 생성 + 의존성 설치 + 다크 테마 기본 설정

**Files:**
- Create: `mobile/` 전체 (create-expo-app이 생성)
- Modify: `mobile/app.json`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `mobile/` 프로젝트 스캐폴드. 이후 모든 태스크가 이 디렉토리 기준으로 파일을 추가한다.

- [ ] **Step 1: Expo 프로젝트 생성**

저장소 루트에서:

```bash
cd /Users/kunhee/Developer/review-docter
npx create-expo-app@latest mobile --template blank-typescript
```

- [ ] **Step 2: 내비게이션·보안저장 의존성 설치**

```bash
cd mobile
npx expo install @react-navigation/native @react-navigation/bottom-tabs react-native-screens react-native-safe-area-context expo-secure-store
```

(`@expo/vector-icons`는 Expo SDK에 기본 포함돼 있어 별도 설치가 필요 없다.)

- [ ] **Step 3: app.json에 다크 테마 기본값 설정**

`mobile/app.json`을 열어 `expo` 객체에 아래 필드를 추가/수정한다 (기존 필드는 유지):

```json
{
  "expo": {
    "name": "Delivery Review",
    "slug": "delivery-review-mobile",
    "userInterfaceStyle": "dark",
    "backgroundColor": "#0b0e14",
    "splash": {
      "backgroundColor": "#0b0e14"
    },
    "ios": {
      "supportsTablet": false,
      "bundleIdentifier": "com.reviewdocter.mobile"
    }
  }
}
```

- [ ] **Step 4: 타입체크 확인**

```bash
npx tsc --noEmit
```

Expected: 에러 없음 (create-expo-app 기본 템플릿은 타입 에러가 없어야 한다).

- [ ] **Step 5: iOS 시뮬레이터에서 기본 화면 부팅 확인**

```bash
npx expo run:ios
```

Expected: Xcode 빌드가 끝나고 iOS 시뮬레이터가 열리며, Expo 기본 템플릿 화면("Open up App.tsx...")이 다크 배경으로 뜬다. 여기서 Xcode 툴체인 문제가 있으면 이후 태스크 전에 미리 잡을 수 있다.

- [ ] **Step 6: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile
git commit -m "chore: mobile/ Expo 프로젝트 스캐폴딩 + 다크 테마 기본 설정"
```

---

### Task 2: 디자인 시스템 + 공용 컴포넌트

**Files:**
- Create: `mobile/src/theme.ts`
- Create: `mobile/src/components/Card.tsx`
- Create: `mobile/src/components/StatTile.tsx`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `colors: { background, surface, surface2, borderSubtle, foreground, muted, accent, accentSoft, success, warning, danger, dangerSoft: string }` from `src/theme.ts`
  - `won(n: number): string`, `percent(n: number): string` from `src/theme.ts`
  - `Card(props: { title?: string; children: React.ReactNode; style?: ViewStyle }): JSX.Element` from `src/components/Card.tsx`
  - `StatTile(props: { label: string; value: string; sublabel?: string; valueColor?: string }): JSX.Element` from `src/components/StatTile.tsx`

- [ ] **Step 1: theme.ts 작성**

`mobile/src/theme.ts`:

```ts
export const colors = {
  background: "#0b0e14",
  surface: "#12161f",
  surface2: "#171c27",
  borderSubtle: "#232935",
  foreground: "#e7e9ee",
  muted: "#8b93a7",
  accent: "#6d5ef5",
  accentSoft: "#6d5ef526",
  success: "#34d399",
  warning: "#fbbf24",
  danger: "#f87171",
  dangerSoft: "#f8717126",
};

export const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;
export const percent = (n: number) => `${(n * 100).toFixed(1)}%`;
```

- [ ] **Step 2: Card 컴포넌트 작성**

`mobile/src/components/Card.tsx`:

```tsx
import { ReactNode } from "react";
import { StyleSheet, Text, View, ViewStyle } from "react-native";
import { colors } from "../theme";

export function Card({
  title,
  children,
  style,
}: {
  title?: string;
  children: ReactNode;
  style?: ViewStyle;
}) {
  return (
    <View style={[styles.card, style]}>
      {title && <Text style={styles.title}>{title}</Text>}
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    backgroundColor: colors.surface,
    padding: 20,
    marginBottom: 16,
  },
  title: {
    fontSize: 13,
    fontWeight: "600",
    color: colors.foreground,
    marginBottom: 12,
  },
});
```

- [ ] **Step 3: StatTile 컴포넌트 작성**

`mobile/src/components/StatTile.tsx`:

```tsx
import { StyleSheet, Text, View } from "react-native";
import { colors } from "../theme";

export function StatTile({
  label,
  value,
  sublabel,
  valueColor,
}: {
  label: string;
  value: string;
  sublabel?: string;
  valueColor?: string;
}) {
  return (
    <View style={styles.tile}>
      <Text style={styles.label}>{label}</Text>
      <Text style={[styles.value, valueColor ? { color: valueColor } : null]}>{value}</Text>
      {sublabel && <Text style={styles.sublabel}>{sublabel}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  tile: {
    flex: 1,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    backgroundColor: colors.surface,
    padding: 16,
  },
  label: { fontSize: 12, color: colors.muted, marginBottom: 6 },
  value: { fontSize: 22, fontWeight: "700", color: colors.foreground },
  sublabel: { fontSize: 11, color: colors.muted, marginTop: 4 },
});
```

- [ ] **Step 4: 타입체크**

```bash
cd mobile && npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 5: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile/src/theme.ts mobile/src/components
git commit -m "feat(mobile): 디자인 토큰 + Card/StatTile 공용 컴포넌트"
```

---

### Task 3: API 클라이언트 + 인증 저장소

**Files:**
- Create: `mobile/src/lib/auth.ts`
- Create: `mobile/src/lib/api.ts`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `getToken(): Promise<string | null>`, `setToken(token: string): Promise<void>`, `clearToken(): Promise<void>` from `src/lib/auth.ts`
  - `apiGet<T>(path: string): Promise<T>`, `apiPost<T>(path: string, body?: unknown): Promise<T>`, `class ApiError extends Error { status: number }`, `setUnauthorizedHandler(handler: () => void): void` from `src/lib/api.ts`

- [ ] **Step 1: 토큰 저장소 작성**

`mobile/src/lib/auth.ts`:

```ts
import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "dris_token";

export function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export function setToken(token: string): Promise<void> {
  return SecureStore.setItemAsync(TOKEN_KEY, token);
}

export function clearToken(): Promise<void> {
  return SecureStore.deleteItemAsync(TOKEN_KEY);
}
```

- [ ] **Step 2: API 클라이언트 작성**

`mobile/src/lib/api.ts` — 웹 `frontend/src/lib/api.ts`와 동일한 구조. `window.location` 리다이렉트 대신 콜백으로 401을 알린다:

```ts
import { clearToken, getToken } from "./auth";

const API_BASE = "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });

  if (res.status === 401) {
    await clearToken();
    onUnauthorized?.();
    throw new ApiError(401, "로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? `요청 실패 (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const apiGet = <T,>(path: string) => request<T>(path);
export const apiPost = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
```

- [ ] **Step 3: 타입체크**

```bash
cd mobile && npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 4: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile/src/lib
git commit -m "feat(mobile): API 클라이언트 + expo-secure-store 인증 저장소"
```

---

### Task 4: 대시보드 화면

**Files:**
- Create: `mobile/src/screens/DashboardScreen.tsx`

**Interfaces:**
- Consumes: `apiGet` (Task 3), `colors`/`won`/`percent` (Task 2), `Card`/`StatTile` (Task 2)
- Produces: `DashboardScreen(): JSX.Element` from `src/screens/DashboardScreen.tsx`

백엔드 응답 필드는 `backend/app/routers/dashboard.py`의 `GET /dashboard`, `GET /alerts`와
`backend/app/routers/sales.py`의 `GET /sales/summary`, `GET /deposits/summary`를 그대로 따른다.

- [ ] **Step 1: DashboardScreen.tsx 작성**

`mobile/src/screens/DashboardScreen.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { Card } from "../components/Card";
import { StatTile } from "../components/StatTile";
import { apiGet } from "../lib/api";
import { colors, percent, won } from "../theme";

type Period = "day" | "week" | "month" | "this_month";
const PERIODS: { key: Period; label: string }[] = [
  { key: "day", label: "오늘" },
  { key: "week", label: "1주" },
  { key: "month", label: "1개월" },
  { key: "this_month", label: "이번달" },
];

type DashboardResponse = {
  store: { id: number; name: string; category: string };
  sales_today: number;
  deposit_today: number;
  unanswered_reviews: number;
  repurchase_rate_adjusted: number | null;
  ad_performance: { campaign_id: number; category: string; acos: number | null; score: number | null } | null;
  unread_alerts: number;
};
type SummaryResponse = { period: string; from_date: string; to_date: string; total_sales?: number; total_deposit?: number };
type AlertItem = { id: number; alert_type: string; message: string; is_read: boolean; created_at: string };

const ALERT_LABEL: Record<string, { label: string; color: string }> = {
  negative_review: { label: "부정 리뷰", color: colors.danger },
  unanswered_review: { label: "미답변", color: colors.warning },
  rank_drop: { label: "순위 하락", color: colors.danger },
};

export function DashboardScreen() {
  const [period, setPeriod] = useState<Period>("week");
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [sales, setSales] = useState<SummaryResponse | null>(null);
  const [deposits, setDeposits] = useState<SummaryResponse | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const [d, a] = await Promise.all([
      apiGet<DashboardResponse>("/dashboard"),
      apiGet<AlertItem[]>("/alerts"),
    ]);
    setDashboard(d);
    setAlerts(a.slice(0, 5));
  }, []);

  const loadPeriod = useCallback(async () => {
    const [s, dep] = await Promise.all([
      apiGet<SummaryResponse>(`/sales/summary?period=${period}`),
      apiGet<SummaryResponse>(`/deposits/summary?period=${period}`),
    ]);
    setSales(s);
    setDeposits(dep);
  }, [period]);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    loadPeriod();
  }, [loadPeriod]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([load(), loadPeriod()]);
    setRefreshing(false);
  }, [load, loadPeriod]);

  if (!dashboard) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>불러오는 중...</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
    >
      <Text style={styles.storeName}>{dashboard.store.name}</Text>
      <Text style={styles.muted}>{dashboard.store.category} · Mock 데이터</Text>

      <View style={styles.periodRow}>
        {PERIODS.map((p) => (
          <View
            key={p.key}
            style={[styles.periodButton, period === p.key && styles.periodButtonActive]}
            onTouchEnd={() => setPeriod(p.key)}
          >
            <Text style={[styles.periodButtonText, period === p.key && styles.periodButtonTextActive]}>{p.label}</Text>
          </View>
        ))}
      </View>

      <View style={styles.row}>
        <StatTile
          label="우가클 점수"
          value={dashboard.ad_performance?.score != null ? `${dashboard.ad_performance.score}점` : "—"}
          sublabel={`ACoS ${dashboard.ad_performance?.acos ?? "—"}%`}
          valueColor={colors.accent}
        />
        <StatTile
          label="재주문율 (보정 후)"
          value={dashboard.repurchase_rate_adjusted !== null ? percent(dashboard.repurchase_rate_adjusted) : "—"}
          sublabel="최근 7일 합산"
        />
      </View>

      <View style={styles.row}>
        <StatTile label="매출" value={sales ? won(sales.total_sales ?? 0) : "…"} sublabel={sales ? `${sales.from_date} ~ ${sales.to_date}` : ""} />
        <StatTile label="입금" value={deposits ? won(deposits.total_deposit ?? 0) : "…"} sublabel="정산 지연 반영 (D+3 가정)" valueColor={colors.success} />
      </View>

      <Card title="답글 대기 리뷰">
        <Text style={styles.warningValue}>{dashboard.unanswered_reviews}건</Text>
      </Card>

      <Card title={`알림 (${dashboard.unread_alerts}건 안읽음)`}>
        {alerts.length === 0 ? (
          <Text style={styles.muted}>알림이 없습니다.</Text>
        ) : (
          alerts.map((a) => {
            const meta = ALERT_LABEL[a.alert_type] ?? { label: a.alert_type, color: colors.muted };
            return (
              <View key={a.id} style={styles.alertRow}>
                <Text style={[styles.alertBadge, { color: meta.color }]}>{meta.label}</Text>
                <Text style={styles.alertMessage}>{a.message}</Text>
              </View>
            );
          })
        )}
      </Card>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, paddingBottom: 32 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },
  muted: { color: colors.muted, fontSize: 12 },
  storeName: { fontSize: 20, fontWeight: "700", color: colors.foreground, marginBottom: 2 },
  periodRow: { flexDirection: "row", gap: 8, marginVertical: 16 },
  periodButton: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: colors.borderSubtle },
  periodButtonActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  periodButtonText: { fontSize: 12, color: colors.muted, fontWeight: "500" },
  periodButtonTextActive: { color: "#fff" },
  row: { flexDirection: "row", gap: 12, marginBottom: 12 },
  warningValue: { fontSize: 22, fontWeight: "700", color: colors.warning },
  alertRow: { flexDirection: "row", alignItems: "flex-start", gap: 8, marginBottom: 8 },
  alertBadge: { fontSize: 11, fontWeight: "600" },
  alertMessage: { fontSize: 12, color: colors.muted, flex: 1 },
});
```

`Pressable` 대신 `View`의 `onTouchEnd`를 임시로 쓰지 않는다 — 대신 `Pressable`을 쓴다. 위 코드의
기간 토글 블록을 아래로 교체한다 (React Native에서 탭 가능한 요소는 `Pressable`이 표준):

```tsx
      <View style={styles.periodRow}>
        {PERIODS.map((p) => (
          <Pressable
            key={p.key}
            onPress={() => setPeriod(p.key)}
            style={[styles.periodButton, period === p.key && styles.periodButtonActive]}
          >
            <Text style={[styles.periodButtonText, period === p.key && styles.periodButtonTextActive]}>{p.label}</Text>
          </Pressable>
        ))}
      </View>
```

그리고 최상단 import를 `import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";`로 바꾼다.

- [ ] **Step 2: 타입체크**

```bash
cd mobile && npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 3: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile/src/screens/DashboardScreen.tsx
git commit -m "feat(mobile): 대시보드 화면"
```

---

### Task 5: 리뷰 관리 화면

**Files:**
- Create: `mobile/src/screens/ReviewsScreen.tsx`

**Interfaces:**
- Consumes: `apiGet`, `apiPost` (Task 3), `colors` (Task 2)
- Produces: `ReviewsScreen(): JSX.Element` from `src/screens/ReviewsScreen.tsx`

응답 필드는 `backend/app/routers/reviews.py`의 `GET /reply-styles`, `GET /reviews`,
`POST /reviews/{id}/generate-reply`, `POST /reviews/{id}/reply`를 그대로 따른다.

- [ ] **Step 1: ReviewsScreen.tsx 작성**

`mobile/src/screens/ReviewsScreen.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { apiGet, apiPost } from "../lib/api";
import { colors } from "../theme";

type ReplyStyle = { id: number; name: string; description: string };
type ReplyRef = { content: string; style_id: number } | null;
type ReviewItem = {
  id: number;
  platform_name: string;
  menu_summary: string;
  rating: number;
  content: string;
  customer_nickname: string;
  customer_order_count: number;
  status: "unanswered" | "pending" | "answered";
  created_at: string;
  final_reply: ReplyRef;
  draft_reply: ReplyRef;
};

const FILTERS = [
  { key: "unanswered", label: "답글 대기" },
  { key: "pending", label: "검토 중" },
  { key: "answered", label: "답글 완료" },
  { key: "", label: "전체" },
] as const;

function ReviewCard({
  review,
  replyStyles,
  onSaved,
}: {
  review: ReviewItem;
  replyStyles: ReplyStyle[];
  onSaved: () => void;
}) {
  const [styleId, setStyleId] = useState(review.draft_reply?.style_id ?? replyStyles[0]?.id ?? 0);
  const [draft, setDraft] = useState(review.draft_reply?.content ?? "");
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (replyStyles.length > 0 && styleId === 0) setStyleId(replyStyles[0].id);
  }, [replyStyles, styleId]);

  const generate = async () => {
    setGenerating(true);
    try {
      const res = await apiPost<{ content: string }>(`/reviews/${review.id}/generate-reply`, { style_id: styleId });
      setDraft(res.content);
    } finally {
      setGenerating(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPost(`/reviews/${review.id}/reply`, { style_id: styleId, content: draft });
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={s.card}>
      <View style={s.metaRow}>
        <Text style={s.platformBadge}>{review.platform_name}</Text>
        <Text style={s.stars}>
          {"★".repeat(review.rating)}
          {"☆".repeat(5 - review.rating)}
        </Text>
        <Text style={s.nickname}>{review.customer_nickname}</Text>
        <Text style={s.muted}>· {review.customer_order_count}회 주문</Text>
      </View>
      <Text style={s.menu}>{review.menu_summary}</Text>
      <Text style={s.content}>{review.content}</Text>

      {review.final_reply ? (
        <View style={s.finalBox}>
          <Text style={s.finalLabel}>등록된 답글</Text>
          <Text style={s.finalContent}>{review.final_reply.content}</Text>
        </View>
      ) : (
        <View style={s.replyArea}>
          <View style={s.styleRow}>
            {replyStyles.map((rs) => (
              <Pressable
                key={rs.id}
                onPress={() => setStyleId(rs.id)}
                style={[s.styleChip, styleId === rs.id && s.styleChipActive]}
              >
                <Text style={[s.styleChipText, styleId === rs.id && s.styleChipTextActive]}>{rs.name}</Text>
              </Pressable>
            ))}
          </View>
          <Pressable onPress={generate} disabled={generating} style={s.generateButton}>
            {generating ? <ActivityIndicator color="#fff" size="small" /> : <Text style={s.generateButtonText}>답글 생성 (Mock)</Text>}
          </Pressable>

          {draft.length > 0 && (
            <View style={s.draftBox}>
              <Text style={s.draftLabel}>미리보기 — 등록 전 자유롭게 수정하세요</Text>
              <TextInput style={s.draftInput} value={draft} onChangeText={setDraft} multiline numberOfLines={3} />
              <View style={s.draftActions}>
                <Pressable onPress={save} disabled={saving || !draft.trim()} style={s.saveButton}>
                  {saving ? <ActivityIndicator color="#fff" size="small" /> : <Text style={s.saveButtonText}>이대로 답글 등록</Text>}
                </Pressable>
                <Pressable onPress={() => setDraft("")} disabled={saving} style={s.cancelButton}>
                  <Text style={s.cancelButtonText}>취소</Text>
                </Pressable>
              </View>
            </View>
          )}
        </View>
      )}
    </View>
  );
}

export function ReviewsScreen() {
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [replyStyles, setReplyStyles] = useState<ReplyStyle[]>([]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("unanswered");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = filter ? `?status=${filter}` : "";
      setReviews(await apiGet<ReviewItem[]>(`/reviews${qs}`));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    apiGet<ReplyStyle[]>("/reply-styles").then(setReplyStyles);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <View style={s.screen}>
      <View style={s.filterRow}>
        {FILTERS.map((f) => (
          <Pressable key={f.key} onPress={() => setFilter(f.key)} style={[s.filterChip, filter === f.key && s.filterChipActive]}>
            <Text style={[s.filterChipText, filter === f.key && s.filterChipTextActive]}>{f.label}</Text>
          </Pressable>
        ))}
      </View>
      <FlatList
        data={reviews}
        keyExtractor={(r) => String(r.id)}
        contentContainerStyle={s.listContent}
        refreshing={loading}
        onRefresh={load}
        renderItem={({ item }) => <ReviewCard review={item} replyStyles={replyStyles} onSaved={load} />}
        ListEmptyComponent={<Text style={s.muted}>해당하는 리뷰가 없습니다.</Text>}
      />
    </View>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background, padding: 16 },
  filterRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  filterChip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: colors.borderSubtle },
  filterChipActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  filterChipText: { fontSize: 12, color: colors.muted, fontWeight: "500" },
  filterChipTextActive: { color: "#fff" },
  listContent: { paddingBottom: 32 },
  muted: { color: colors.muted, fontSize: 12 },
  card: { borderRadius: 12, borderWidth: 1, borderColor: colors.borderSubtle, backgroundColor: colors.surface2, padding: 14, marginBottom: 12 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" },
  platformBadge: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.accent,
    backgroundColor: colors.surface,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  stars: { fontSize: 12, color: colors.warning },
  nickname: { fontSize: 12, fontWeight: "600", color: colors.foreground },
  menu: { fontSize: 11, color: colors.muted, marginTop: 6 },
  content: { fontSize: 13, color: colors.foreground, marginTop: 2 },
  finalBox: { marginTop: 10, borderRadius: 10, borderWidth: 1, borderColor: colors.borderSubtle, backgroundColor: colors.surface, padding: 10 },
  finalLabel: { fontSize: 11, fontWeight: "600", color: colors.success, marginBottom: 4 },
  finalContent: { fontSize: 13, color: colors.foreground },
  replyArea: { marginTop: 10, gap: 8 },
  styleRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  styleChip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 8, borderWidth: 1, borderColor: colors.borderSubtle, backgroundColor: colors.surface },
  styleChipActive: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  styleChipText: { fontSize: 11, color: colors.muted },
  styleChipTextActive: { color: colors.accent, fontWeight: "600" },
  generateButton: { backgroundColor: colors.accent, borderRadius: 8, paddingVertical: 8, alignItems: "center" },
  generateButtonText: { color: "#fff", fontSize: 12, fontWeight: "600" },
  draftBox: { borderRadius: 10, borderWidth: 1, borderColor: colors.accent, backgroundColor: colors.accentSoft, padding: 10, gap: 8 },
  draftLabel: { fontSize: 11, fontWeight: "600", color: colors.accent },
  draftInput: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    backgroundColor: colors.surface,
    padding: 8,
    fontSize: 13,
    color: colors.foreground,
    textAlignVertical: "top",
  },
  draftActions: { flexDirection: "row", gap: 8 },
  saveButton: { backgroundColor: colors.success, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8 },
  saveButtonText: { color: "#fff", fontSize: 12, fontWeight: "600" },
  cancelButton: { borderRadius: 8, borderWidth: 1, borderColor: colors.borderSubtle, paddingHorizontal: 12, paddingVertical: 8 },
  cancelButtonText: { color: colors.muted, fontSize: 12 },
});
```

- [ ] **Step 2: 타입체크**

```bash
cd mobile && npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 3: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile/src/screens/ReviewsScreen.tsx
git commit -m "feat(mobile): 리뷰 관리 화면 (답글 생성/등록 플로우)"
```

---

### Task 6: 광고 순위 모니터링 화면

**Files:**
- Create: `mobile/src/screens/AdsScreen.tsx`

**Interfaces:**
- Consumes: `apiGet` (Task 3), `Card` (Task 2), `colors`/`won` (Task 2)
- Produces: `AdsScreen(): JSX.Element` from `src/screens/AdsScreen.tsx`

응답 필드는 `backend/app/routers/ads.py`의 `GET /ads/rank-monitoring`, `GET /ads/rank-by-distance`를
그대로 따른다. `POST /ads/rank-by-distance/run`은 쓰지 않는다(스펙: 조회 전용).

- [ ] **Step 1: AdsScreen.tsx 작성**

`mobile/src/screens/AdsScreen.tsx`:

```tsx
import { useEffect, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { Card } from "../components/Card";
import { apiGet } from "../lib/api";
import { colors, won } from "../theme";

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
type DistancePoint = {
  point_label: string;
  distance_km: number;
  current_rank: number;
  total_scanned: number;
  ads_above: number;
  snapshot_at: string;
};
type DistanceRankRow = { campaign_id: number; category: string; target_rank: number; points: DistancePoint[] };

const ACTION_LABEL: Record<string, string> = { keep: "유지", raise_cpc: "CPC 인상 권장", lower_cpc: "CPC 인하 권장" };

export function AdsScreen() {
  const [ranks, setRanks] = useState<RankRow[]>([]);
  const [distanceRanks, setDistanceRanks] = useState<DistanceRankRow[]>([]);

  useEffect(() => {
    apiGet<RankRow[]>("/ads/rank-monitoring").then(setRanks);
    apiGet<DistanceRankRow[]>("/ads/rank-by-distance").then(setDistanceRanks);
  }, []);

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.content}>
      <Text style={s.heading}>광고 순위 모니터링</Text>
      <Text style={s.muted}>순위 현황은 Mock 스냅샷, 반경별 순위만 실기기로 실측한 값입니다.</Text>

      <Card title="순위 현황 (Mock)">
        {ranks.length === 0 ? (
          <Text style={s.muted}>등록된 광고 캠페인이 없습니다.</Text>
        ) : (
          ranks.map((r) => {
            const dropped = r.rank_status === "rank_dropped";
            return (
              <View key={r.campaign_id} style={s.rankRow}>
                <View style={s.rankHeader}>
                  <Text style={s.category}>{r.category}</Text>
                  <Text style={[s.rankValue, { color: dropped ? colors.danger : colors.success }]}>
                    {r.current_rank === null ? "—" : `${r.current_rank}위`}
                  </Text>
                </View>
                <Text style={s.muted}>
                  현재 CPC {won(r.current_cpc)} · 목표 {r.target_rank}위
                </Text>
                <Text style={s.muted}>
                  {dropped ? "순위 밀림" : "정상"} · 추천: {ACTION_LABEL[r.recommended_action]}
                  {r.suggested_cpc ? ` (${won(r.suggested_cpc)})` : ""}
                </Text>
              </View>
            );
          })
        )}
      </Card>

      <Card title="반경별 실측 순위">
        {distanceRanks.length === 0 ? (
          <Text style={s.muted}>등록된 광고 캠페인이 없습니다.</Text>
        ) : (
          distanceRanks.map((c) => (
            <View key={c.campaign_id} style={s.distanceBlock}>
              <Text style={s.category}>{c.category}</Text>
              {c.points.length === 0 ? (
                <Text style={s.muted}>아직 실측 데이터가 없습니다.</Text>
              ) : (
                c.points.map((p) => (
                  <View key={p.point_label} style={s.pointRow}>
                    <Text style={s.pointLabel}>{p.point_label}</Text>
                    <Text style={[s.rankValue, { color: p.current_rank > c.target_rank ? colors.danger : colors.success }]}>
                      {p.current_rank}위
                    </Text>
                    <Text style={s.muted}>
                      스캔 {p.total_scanned}개 · 위 광고 {p.ads_above}개
                    </Text>
                  </View>
                ))
              )}
            </View>
          ))
        )}
      </Card>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, paddingBottom: 32 },
  heading: { fontSize: 18, fontWeight: "700", color: colors.foreground, marginBottom: 4 },
  muted: { color: colors.muted, fontSize: 12, marginBottom: 4 },
  rankRow: { paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: colors.borderSubtle },
  rankHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 4 },
  category: { fontSize: 14, fontWeight: "600", color: colors.foreground },
  rankValue: { fontSize: 15, fontWeight: "700" },
  distanceBlock: { marginBottom: 16 },
  pointRow: { paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.borderSubtle },
  pointLabel: { fontSize: 13, color: colors.foreground, marginBottom: 2 },
});
```

- [ ] **Step 2: 타입체크**

```bash
cd mobile && npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 3: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile/src/screens/AdsScreen.tsx
git commit -m "feat(mobile): 광고 순위 모니터링 화면 (조회 전용)"
```

---

### Task 7: 매출 화면

**Files:**
- Create: `mobile/src/screens/SalesScreen.tsx`

**Interfaces:**
- Consumes: `apiGet` (Task 3), `Card` (Task 2), `colors`/`won` (Task 2)
- Produces: `SalesScreen(): JSX.Element` from `src/screens/SalesScreen.tsx`

응답 필드는 `backend/app/routers/sales.py`의 `GET /sales/summary`, `GET /deposits/summary`,
`GET /sales/daily`, `GET /sales/breakdown`을 그대로 따른다.

- [ ] **Step 1: SalesScreen.tsx 작성**

`mobile/src/screens/SalesScreen.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Card } from "../components/Card";
import { apiGet } from "../lib/api";
import { colors, won } from "../theme";

type Period = "day" | "week" | "month" | "this_month";
const PERIODS: { key: Period; label: string }[] = [
  { key: "day", label: "오늘" },
  { key: "week", label: "1주" },
  { key: "month", label: "1개월" },
  { key: "this_month", label: "이번달" },
];

type SummaryResponse = { period: string; from_date: string; to_date: string; total_sales?: number; total_deposit?: number };
type DailyRow = { date: string; amount: number };
type BreakdownRow = {
  platform_id: number;
  platform_name: string;
  sales_amount: number;
  commission_estimate: number;
  payment_fee_estimate: number;
  net_estimate: number;
  actual_deposit: number;
};
type BreakdownResponse = { period: string; from_date: string; to_date: string; platforms: BreakdownRow[] };

export function SalesScreen() {
  const [period, setPeriod] = useState<Period>("week");
  const [sales, setSales] = useState<SummaryResponse | null>(null);
  const [deposits, setDeposits] = useState<SummaryResponse | null>(null);
  const [daily, setDaily] = useState<DailyRow[]>([]);
  const [breakdown, setBreakdown] = useState<BreakdownResponse | null>(null);

  const load = useCallback(async () => {
    const [s, d, daily14, b] = await Promise.all([
      apiGet<SummaryResponse>(`/sales/summary?period=${period}`),
      apiGet<SummaryResponse>(`/deposits/summary?period=${period}`),
      apiGet<DailyRow[]>("/sales/daily?days=14"),
      apiGet<BreakdownResponse>(`/sales/breakdown?period=${period}`),
    ]);
    setSales(s);
    setDeposits(d);
    setDaily([...daily14].reverse());
    setBreakdown(b);
  }, [period]);

  useEffect(() => {
    load();
  }, [load]);

  const max = Math.max(1, ...daily.map((r) => r.amount));

  return (
    <ScrollView style={s2.screen} contentContainerStyle={s2.content}>
      <Text style={s2.heading}>매출</Text>

      <View style={s2.periodRow}>
        {PERIODS.map((p) => (
          <Pressable key={p.key} onPress={() => setPeriod(p.key)} style={[s2.periodButton, period === p.key && s2.periodButtonActive]}>
            <Text style={[s2.periodButtonText, period === p.key && s2.periodButtonTextActive]}>{p.label}</Text>
          </Pressable>
        ))}
      </View>

      <View style={s2.row}>
        <Card title="매출" style={s2.half}>
          <Text style={s2.bigValue}>{sales ? won(sales.total_sales ?? 0) : "…"}</Text>
        </Card>
        <Card title="입금" style={s2.half}>
          <Text style={[s2.bigValue, { color: colors.success }]}>{deposits ? won(deposits.total_deposit ?? 0) : "…"}</Text>
        </Card>
      </View>

      <Card title="최근 14일 매출 추이">
        {daily.length === 0 ? (
          <Text style={s2.muted}>데이터가 없습니다.</Text>
        ) : (
          daily.map((r) => (
            <View key={r.date} style={s2.barRow}>
              <Text style={s2.barDate}>{r.date.slice(5)}</Text>
              <View style={s2.barTrack}>
                <View style={[s2.barFill, { width: `${(r.amount / max) * 100}%` }]} />
              </View>
              <Text style={s2.barAmount}>{won(r.amount)}</Text>
            </View>
          ))
        )}
      </Card>

      <Card title="플랫폼별 내역">
        {!breakdown || breakdown.platforms.length === 0 ? (
          <Text style={s2.muted}>해당 기간 매출 데이터가 없습니다.</Text>
        ) : (
          breakdown.platforms.map((p) => (
            <View key={p.platform_id} style={s2.platformBlock}>
              <Text style={s2.platformName}>{p.platform_name}</Text>
              <View style={s2.platformLine}>
                <Text style={s2.muted}>매출액</Text>
                <Text style={s2.platformValue}>{won(p.sales_amount)}</Text>
              </View>
              <View style={s2.platformLine}>
                <Text style={[s2.muted, { color: colors.danger }]}>− 중개수수료(추정)</Text>
                <Text style={[s2.platformValue, { color: colors.danger }]}>−{won(p.commission_estimate)}</Text>
              </View>
              <View style={s2.platformLine}>
                <Text style={[s2.muted, { color: colors.danger }]}>− 결제수수료(추정)</Text>
                <Text style={[s2.platformValue, { color: colors.danger }]}>−{won(p.payment_fee_estimate)}</Text>
              </View>
              <View style={[s2.platformLine, s2.platformLineTotal]}>
                <Text style={s2.platformLabelBold}>추정 정산액</Text>
                <Text style={s2.platformValueBold}>{won(p.net_estimate)}</Text>
              </View>
              <View style={s2.platformLine}>
                <Text style={[s2.muted, { color: colors.success }]}>실제 입금액</Text>
                <Text style={[s2.platformValue, { color: colors.success }]}>{won(p.actual_deposit)}</Text>
              </View>
            </View>
          ))
        )}
      </Card>
    </ScrollView>
  );
}

const s2 = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, paddingBottom: 32 },
  heading: { fontSize: 18, fontWeight: "700", color: colors.foreground, marginBottom: 12 },
  periodRow: { flexDirection: "row", gap: 8, marginBottom: 16 },
  periodButton: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: colors.borderSubtle },
  periodButtonActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  periodButtonText: { fontSize: 12, color: colors.muted, fontWeight: "500" },
  periodButtonTextActive: { color: "#fff" },
  row: { flexDirection: "row", gap: 12 },
  half: { flex: 1 },
  bigValue: { fontSize: 20, fontWeight: "700", color: colors.foreground },
  muted: { color: colors.muted, fontSize: 12 },
  barRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  barDate: { width: 40, fontSize: 11, color: colors.muted },
  barTrack: { flex: 1, height: 8, borderRadius: 4, backgroundColor: colors.surface2, overflow: "hidden" },
  barFill: { height: "100%", borderRadius: 4, backgroundColor: colors.accent },
  barAmount: { width: 84, textAlign: "right", fontSize: 11, color: colors.foreground },
  platformBlock: { borderRadius: 10, borderWidth: 1, borderColor: colors.borderSubtle, padding: 12, marginBottom: 10 },
  platformName: { fontSize: 13, fontWeight: "600", color: colors.accent, marginBottom: 6 },
  platformLine: { flexDirection: "row", justifyContent: "space-between", marginBottom: 2 },
  platformValue: { fontSize: 12, color: colors.foreground },
  platformLineTotal: { borderTopWidth: 1, borderTopColor: colors.borderSubtle, paddingTop: 4, marginTop: 2 },
  platformLabelBold: { fontSize: 12, fontWeight: "700", color: colors.foreground },
  platformValueBold: { fontSize: 12, fontWeight: "700", color: colors.foreground },
});
```

- [ ] **Step 2: 타입체크**

```bash
cd mobile && npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 3: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile/src/screens/SalesScreen.tsx
git commit -m "feat(mobile): 매출 화면 (기간 토글 + 추이 + 플랫폼별 내역)"
```

---

### Task 8: 로그인 화면

**Files:**
- Create: `mobile/src/screens/LoginScreen.tsx`

**Interfaces:**
- Consumes: `apiPost`, `ApiError` (Task 3), `setToken` (Task 3), `colors` (Task 2)
- Produces: `LoginScreen(props: { onLoggedIn: (token: string) => void }): JSX.Element` from `src/screens/LoginScreen.tsx`

응답 필드는 `backend/app/routers/auth.py`의 `POST /auth/login`을 그대로 따른다.

- [ ] **Step 1: LoginScreen.tsx 작성**

`mobile/src/screens/LoginScreen.tsx`:

```tsx
import { useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { apiPost, ApiError } from "../lib/api";
import { setToken } from "../lib/auth";
import { colors } from "../theme";

type LoginResponse = { access_token: string; token_type: string; user: { id: number; email: string; nickname: string } };

export function LoginScreen({ onLoggedIn }: { onLoggedIn: (token: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const login = async (loginEmail: string, loginPassword: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiPost<LoginResponse>("/auth/login", { email: loginEmail, password: loginPassword });
      await setToken(res.access_token);
      onLoggedIn(res.access_token);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "로그인 중 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <Text style={styles.title}>Delivery Review</Text>
        <Text style={styles.subtitle}>& Store Insight</Text>

        <Text style={styles.label}>이메일</Text>
        <TextInput
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          placeholder="you@store.com"
          placeholderTextColor={colors.muted}
          autoCapitalize="none"
          keyboardType="email-address"
        />
        <Text style={styles.label}>비밀번호</Text>
        <TextInput
          style={styles.input}
          value={password}
          onChangeText={setPassword}
          placeholder="********"
          placeholderTextColor={colors.muted}
          secureTextEntry
        />

        {error && <Text style={styles.error}>{error}</Text>}

        <Pressable style={styles.button} onPress={() => login(email, password)} disabled={loading}>
          {loading ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>로그인</Text>}
        </Pressable>
        <Pressable style={styles.demoButton} onPress={() => login("demo@dris.kr", "demo1234!")} disabled={loading}>
          <Text style={styles.demoButtonText}>데모 계정으로 로그인</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background, alignItems: "center", justifyContent: "center", padding: 24 },
  card: {
    width: "100%",
    maxWidth: 360,
    backgroundColor: colors.surface,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    padding: 24,
  },
  title: { fontSize: 20, fontWeight: "700", color: colors.foreground },
  subtitle: { fontSize: 13, color: colors.muted, marginBottom: 24 },
  label: { fontSize: 12, color: colors.muted, marginBottom: 6, marginTop: 12 },
  input: {
    backgroundColor: colors.surface2,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: colors.foreground,
    fontSize: 14,
  },
  error: { color: colors.danger, fontSize: 12, marginTop: 12 },
  button: { backgroundColor: colors.accent, borderRadius: 10, paddingVertical: 12, alignItems: "center", marginTop: 20 },
  buttonText: { color: "#fff", fontWeight: "600", fontSize: 14 },
  demoButton: { borderRadius: 10, borderWidth: 1, borderColor: colors.borderSubtle, paddingVertical: 12, alignItems: "center", marginTop: 10 },
  demoButtonText: { color: colors.foreground, fontWeight: "500", fontSize: 14 },
});
```

- [ ] **Step 2: 타입체크**

```bash
cd mobile && npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 3: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile/src/screens/LoginScreen.tsx
git commit -m "feat(mobile): 로그인 화면"
```

---

### Task 9: 탭 내비게이션 + 앱 진입점 (전체 배선)

**Files:**
- Create: `mobile/src/navigation/RootNavigator.tsx`
- Modify: `mobile/App.tsx`

**Interfaces:**
- Consumes: `DashboardScreen`(Task 4), `ReviewsScreen`(Task 5), `AdsScreen`(Task 6), `SalesScreen`(Task 7), `LoginScreen`(Task 8), `getToken`/`setUnauthorizedHandler`(Task 3), `colors`(Task 2)
- Produces: `RootNavigator(props: { onLogout: () => void }): JSX.Element` from `src/navigation/RootNavigator.tsx`; `App(): JSX.Element` (default export) from `App.tsx`

- [ ] **Step 1: RootNavigator.tsx 작성**

`mobile/src/navigation/RootNavigator.tsx`:

```tsx
import { Ionicons } from "@expo/vector-icons";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { Pressable } from "react-native";
import { AdsScreen } from "../screens/AdsScreen";
import { DashboardScreen } from "../screens/DashboardScreen";
import { ReviewsScreen } from "../screens/ReviewsScreen";
import { SalesScreen } from "../screens/SalesScreen";
import { colors } from "../theme";

export type TabParamList = {
  Dashboard: undefined;
  Reviews: undefined;
  Ads: undefined;
  Sales: undefined;
};

const Tab = createBottomTabNavigator<TabParamList>();

const ICONS: Record<keyof TabParamList, keyof typeof Ionicons.glyphMap> = {
  Dashboard: "grid-outline",
  Reviews: "chatbubble-ellipses-outline",
  Ads: "trending-up-outline",
  Sales: "bar-chart-outline",
};

const LABELS: Record<keyof TabParamList, string> = {
  Dashboard: "대시보드",
  Reviews: "리뷰 관리",
  Ads: "광고 순위",
  Sales: "매출",
};

export function RootNavigator({ onLogout }: { onLogout: () => void }) {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        title: LABELS[route.name as keyof TabParamList],
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.foreground,
        headerShadowVisible: false,
        headerRight: () => (
          <Pressable onPress={onLogout} hitSlop={8} style={{ marginRight: 16 }}>
            <Ionicons name="log-out-outline" size={22} color={colors.muted} />
          </Pressable>
        ),
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.borderSubtle },
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted,
        tabBarIcon: ({ color, size }) => <Ionicons name={ICONS[route.name as keyof TabParamList]} size={size} color={color} />,
      })}
    >
      <Tab.Screen name="Dashboard" component={DashboardScreen} />
      <Tab.Screen name="Reviews" component={ReviewsScreen} />
      <Tab.Screen name="Ads" component={AdsScreen} />
      <Tab.Screen name="Sales" component={SalesScreen} />
    </Tab.Navigator>
  );
}
```

- [ ] **Step 2: App.tsx를 인증 분기 진입점으로 교체**

`mobile/App.tsx` (create-expo-app이 만든 기존 내용을 전부 지우고 아래로 교체):

```tsx
import { useCallback, useEffect, useState } from "react";
import { DarkTheme, NavigationContainer, Theme } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { setUnauthorizedHandler } from "./src/lib/api";
import { getToken } from "./src/lib/auth";
import { RootNavigator } from "./src/navigation/RootNavigator";
import { LoginScreen } from "./src/screens/LoginScreen";
import { colors } from "./src/theme";

const navTheme: Theme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    primary: colors.accent,
    background: colors.background,
    card: colors.surface,
    text: colors.foreground,
    border: colors.borderSubtle,
    notification: colors.danger,
  },
};

export default function App() {
  const [token, setTokenState] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    getToken().then((t) => {
      setTokenState(t);
      setChecking(false);
    });
    setUnauthorizedHandler(() => setTokenState(null));
  }, []);

  const handleLoggedIn = useCallback((newToken: string) => setTokenState(newToken), []);
  const handleLogout = useCallback(() => setTokenState(null), []);

  if (checking) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <NavigationContainer theme={navTheme}>
      <StatusBar style="light" />
      {token ? <RootNavigator onLogout={handleLogout} /> : <LoginScreen onLoggedIn={handleLoggedIn} />}
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },
});
```

`handleLogout`은 상태만 `null`로 바꾸고, 실제 토큰 삭제는 `setUnauthorizedHandler`가 401을 받았을 때
`lib/api.ts`가 이미 `clearToken()`을 호출하므로 중복이 아니다 — 다만 사용자가 로그아웃 버튼을 눌렀을
때도 저장된 토큰을 지워야 하므로, `handleLogout`에서도 `clearToken()`을 호출하도록 아래로 바꾼다:

```tsx
import { clearToken, getToken } from "./src/lib/auth";
// ...
const handleLogout = useCallback(() => {
  clearToken();
  setTokenState(null);
}, []);
```

- [ ] **Step 3: 타입체크**

```bash
cd mobile && npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 4: 커밋**

```bash
cd /Users/kunhee/Developer/review-docter
git add mobile/src/navigation mobile/App.tsx
git commit -m "feat(mobile): 하단 탭 내비게이션 + 인증 분기 진입점 배선"
```

---

### Task 10: iOS 시뮬레이터 통합 검증

**Files:** 없음 (검증 전용 태스크)

**Interfaces:**
- Consumes: Task 1~9의 전체 결과물
- Produces: 없음 — 이 계획의 완료 확인

- [ ] **Step 1: 로컬 백엔드 기동 확인**

```bash
curl -s http://localhost:8000/health
```

Expected: `{"status":"ok"}`. 안 떠 있으면 `cd backend && .venv/bin/uvicorn app.main:app --reload`로 띄운다.

- [ ] **Step 2: iOS 시뮬레이터 실행**

```bash
cd mobile
npx expo run:ios
```

Expected: 빌드 성공 후 시뮬레이터에 로그인 화면이 다크 테마로 뜬다.

- [ ] **Step 3: 로그인 플로우 확인**

시뮬레이터에서 "데모 계정으로 로그인" 버튼을 탭한다.

Expected: 하단 탭 4개(대시보드/리뷰 관리/광고 순위/매출)가 있는 화면으로 전환된다.

- [ ] **Step 4: 4개 화면 데이터 로딩 확인**

탭을 하나씩 눌러 각 화면이 에러 없이 실제 데이터를 렌더링하는지 확인한다:
- 대시보드: 매장명, 우가클 점수, 재주문율, 매출/입금, 답글 대기, 알림이 보이는지
- 리뷰 관리: 리뷰 목록이 보이고, 하나를 선택해 "답글 생성 (Mock)" → 미리보기 → "이대로 답글 등록"까지 실제로 동작하는지
- 광고 순위: 순위 현황(Mock) + 반경별 실측 순위 두 섹션 다 보이는지 (반경별 실측은 이전 세션에서 DB에 적재된 값이 그대로 조회된다)
- 매출: 기간 토글을 눌러보고 값이 바뀌는지, 플랫폼별 내역이 보이는지

- [ ] **Step 5: 로그아웃 확인**

아무 화면에서나 우측 상단 로그아웃 아이콘을 탭한다.

Expected: 로그인 화면으로 돌아간다. 앱을 완전히 종료했다가 다시 열어도 로그인 화면부터 시작하는지
확인한다(토큰이 실제로 지워졌는지 검증).

- [ ] **Step 6: 최종 커밋**

이 태스크는 코드 변경이 없으므로 커밋할 것이 없다. 검증 중 문제를 발견해 코드를 고쳤다면 해당 수정을
커밋한다.

```bash
git status
```

Expected: `mobile/` 관련 변경 없음 (또는 검증 중 발견한 수정 사항만 있음).

---

## Self-Review 완료 사항

- **스펙 커버리지**: 스펙의 "화면 구성" 4개, "데이터 흐름"(토큰 저장/베이스 URL/store_id 생략), "에러 처리"(401 → 로그아웃), "디자인 시스템"(색상 토큰/시스템 폰트/Card 룩/탭바), "테스트"(tsc + 수동 확인) 전부 Task 1~10에 대응된다.
- **타입 일관성**: `apiGet`/`apiPost`/`ApiError`/`setUnauthorizedHandler`(Task 3), `getToken`/`setToken`/`clearToken`(Task 3), `colors`/`won`/`percent`(Task 2), `Card`/`StatTile`(Task 2) — 이후 태스크들이 쓰는 이름과 시그니처가 전부 동일하게 유지된다.
- **플레이스홀더 없음**: 모든 화면·컴포넌트가 실제 백엔드 응답 필드(각 라우터 파일 확인 완료)를 그대로 쓰는 완전한 코드다.
