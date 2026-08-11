"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { Modal } from "@/components/Modal";
import { apiGet, percent, won } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type Period = "day" | "week" | "month" | "this_month";
const PERIODS: { key: Period; label: string }[] = [
  { key: "day", label: "오늘" },
  { key: "week", label: "1주" },
  { key: "month", label: "1개월" },
  { key: "this_month", label: "이번달" },
];

type SummaryResponse = { total_sales?: number; total_deposit?: number; from_date: string; to_date: string };
type DashboardResponse = {
  store: { id: number; name: string; category: string };
  unanswered_reviews: number;
  repurchase_rate_adjusted: number | null;
  ad_performance: { campaign_id: number; category: string; acos: number | null; score: number | null } | null;
  unread_alerts: number;
};
type Alert = { id: number; alert_type: string; message: string; created_at: string };
type AdPerformance = {
  category: string; cpc: number; cvr: number; aov: number; acos: number | null; score: number | null;
  order_share: number | null; clicks: number; ad_orders: number;
};
type ClickPerformance = {
  shop_no: string; period_days: number; ad_spend: number; impressions: number; clicks: number;
  ad_orders: number; ad_revenue: number; cpc: number; cvr: number; aov: number;
  acos: number | null; score: number | null;
};
type ShopBrand = { shop_no: string; shop_name: string };
type DailyRow = { date: string; amount: number };
type RepurchaseRow = { metric_date: string; new_orders: number; repeat_orders: number; rate_raw: number; rate_adjusted: number };
type BreakdownRow = { platform_name: string; sales_amount: number; commission_estimate: number; payment_fee_estimate: number; net_estimate: number; actual_deposit: number };

const ALERT_LABEL: Record<string, { label: string; color: string }> = {
  negative_review: { label: "부정 리뷰", color: "text-danger" },
  unanswered_review: { label: "미답변", color: "text-warning" },
  rank_drop: { label: "순위 하락", color: "text-danger" },
};

function ClickableCard({ title, onClick, children }: { title: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="w-full rounded-2xl border border-border-subtle bg-surface p-5 text-left transition hover:border-accent hover:bg-surface-2"
    >
      <h2 className="mb-4 text-sm font-semibold text-foreground">{title}</h2>
      {children}
    </button>
  );
}

function UgacleModal({ storeId, shopNo }: { storeId: number; shopNo: string }) {
  const [perf, setPerf] = useState<ClickPerformance | null>(null);
  useEffect(() => {
    if (!shopNo) return;
    apiGet<ClickPerformance>(`/ads/click-performance?store_id=${storeId}&shop_no=${shopNo}&days=14`).then(setPerf);
  }, [storeId, shopNo]);

  if (!shopNo) {
    return (
      <p className="text-sm text-muted">
        연결된 배민 브랜드가 없습니다. &quot;가게 연결&quot;에서 배민 계정을 연결한 뒤 데이터 동기화를 실행하세요.
      </p>
    );
  }
  if (perf === null) return <p className="text-sm text-muted">불러오는 중...</p>;
  return (
    <div>
      <p className="mb-4 rounded-lg bg-surface-2 p-3 text-xs text-muted">
        최근 14일 우리가게클릭(배민 실데이터) 성과를 집계한 값입니다.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg bg-surface-2 p-3">
          <p className="text-xs text-muted">주문전환율 (CVR)</p>
          <p className="mt-1 text-lg font-bold">{(perf.cvr * 100).toFixed(1)}%</p>
        </div>
        <div className="rounded-lg bg-surface-2 p-3">
          <p className="text-xs text-muted">주문당 광고비율 (ACoS)</p>
          <p className="mt-1 text-lg font-bold">{perf.acos !== null ? `${perf.acos}%` : "—"}</p>
        </div>
        <div className="rounded-lg bg-surface-2 p-3">
          <p className="text-xs text-muted">노출수</p>
          <p className="mt-1 text-lg font-bold">{perf.impressions.toLocaleString()}회</p>
        </div>
        <div className="rounded-lg bg-surface-2 p-3">
          <p className="text-xs text-muted">클릭당 단가 (CPC)</p>
          <p className="mt-1 text-lg font-bold">{won(Math.round(perf.cpc))}</p>
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between rounded-lg border border-accent/30 bg-accent-soft p-3">
        <span className="text-xs text-muted">종합 성과 점수</span>
        <span className="text-xl font-bold text-accent">{perf.score ?? "—"}점</span>
      </div>
    </div>
  );
}

type PlatformOption = { id: number; code: string; name: string; brand_color: string | null };

function SalesBreakdownModal({ storeId, period }: { storeId: number; period: Period }) {
  const [data, setData] = useState<{ platforms: BreakdownRow[] } | null>(null);
  useEffect(() => {
    apiGet<{ platforms: BreakdownRow[] }>(`/sales/breakdown?period=${period}&store_id=${storeId}`).then(setData);
  }, [storeId, period]);

  if (!data) return <p className="text-sm text-muted">불러오는 중...</p>;
  if (data.platforms.length === 0) return <p className="text-sm text-muted">해당 기간 매출 데이터가 없습니다.</p>;

  return (
    <div className="space-y-4">
      <p className="rounded-lg bg-surface-2 p-3 text-xs text-muted">
        중개수수료·결제수수료는 플랫폼 기본 요율로 추정한 값입니다. 실제 입금액은 정산 주기(D+3)
        차이로 추정치와 다를 수 있습니다 — 이 차이가 &quot;매출과 입금이 다르다&quot;는 현장 문제입니다.
      </p>
      {data.platforms.map((p) => (
        <div key={p.platform_name} className="rounded-lg border border-border-subtle p-4">
          <p className="mb-2 text-sm font-medium text-accent">{p.platform_name}</p>
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between"><dt className="text-muted">매출액</dt><dd>{won(p.sales_amount)}</dd></div>
            <div className="flex justify-between text-danger"><dt>− 중개수수료(추정)</dt><dd>−{won(p.commission_estimate)}</dd></div>
            <div className="flex justify-between text-danger"><dt>− 결제수수료(추정)</dt><dd>−{won(p.payment_fee_estimate)}</dd></div>
            <div className="flex justify-between border-t border-border-subtle pt-1 font-semibold"><dt>추정 정산액</dt><dd>{won(p.net_estimate)}</dd></div>
            <div className="flex justify-between text-success"><dt>실제 입금액</dt><dd>{won(p.actual_deposit)}</dd></div>
          </dl>
        </div>
      ))}
    </div>
  );
}

function RepurchaseModal({ storeId, platformId }: { storeId: number; platformId: number | null }) {
  const [rows, setRows] = useState<RepurchaseRow[]>([]);
  useEffect(() => {
    if (!platformId) return;
    apiGet<RepurchaseRow[]>(`/repurchase/summary?store_id=${storeId}&platform_id=${platformId}&days=14`).then((r) =>
      setRows(r.reverse())
    );
  }, [storeId, platformId]);

  return (
    <div className="space-y-2">
      <p className="mb-2 rounded-lg bg-surface-2 p-3 text-xs text-muted">
        보정 후 재주문율은 해당 날짜 기준 이전 7일 합산 기준입니다.
      </p>
      {rows.map((r) => (
        <div key={r.metric_date} className="flex items-center justify-between rounded-lg border border-border-subtle px-4 py-2.5 text-sm">
          <span className="text-muted">{r.metric_date}</span>
          <span>신규 {r.new_orders} · 재주문 {r.repeat_orders}</span>
          <span className="font-semibold text-accent">{percent(r.rate_adjusted)}</span>
        </div>
      ))}
      {rows.length === 0 && <p className="text-sm text-muted">데이터가 없습니다.</p>}
    </div>
  );
}

function DailyAmountModal({
  storeId, endpoint, positive, platformId,
}: {
  storeId: number; endpoint: string; positive: boolean; platformId: number | null;
}) {
  const [rows, setRows] = useState<DailyRow[]>([]);
  useEffect(() => {
    if (!platformId) return;
    apiGet<DailyRow[]>(`${endpoint}?store_id=${storeId}&platform_id=${platformId}&days=14`).then((r) =>
      setRows([...r].reverse())
    );
  }, [storeId, endpoint, platformId]);

  const max = Math.max(1, ...rows.map((r) => r.amount));
  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div key={r.date} className="flex items-center gap-3">
          <span className="w-24 shrink-0 text-xs text-muted">{r.date}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2">
            <div
              className={`h-full rounded-full ${positive ? "bg-success" : "bg-accent"}`}
              style={{ width: `${(r.amount / max) * 100}%` }}
            />
          </div>
          <span className="w-28 shrink-0 text-right text-sm font-medium">{won(r.amount)}</span>
        </div>
      ))}
      {rows.length === 0 && <p className="text-sm text-muted">데이터가 없습니다.</p>}
    </div>
  );
}

export default function DashboardPage() {
  const { storeId } = useStoreContext();
  const [period, setPeriod] = useState<Period>("week");
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [sales, setSales] = useState<SummaryResponse | null>(null);
  const [deposits, setDeposits] = useState<SummaryResponse | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [openModal, setOpenModal] = useState<"ugacle" | "sales_breakdown" | "repurchase" | "sales_daily" | "deposit_daily" | null>(null);
  const [brands, setBrands] = useState<ShopBrand[]>([]);
  const [selectedShopNo, setSelectedShopNo] = useState("");
  const [clickPerf, setClickPerf] = useState<ClickPerformance | null>(null);
  // 매출/입금/재주문율 요약은 배민 실데이터만 보여준다 — 요기요/쿠팡이츠는
  // 아직 Mock 연동뿐이라 실데이터와 섞으면 숫자가 왜곡된다. "매출 분석" 카드를
  // 클릭해 여는 플랫폼별 비교 상세(SalesBreakdownModal)는 예외 — 거기서는
  // 플랫폼 간 비교 자체가 목적이라 전체를 그대로 보여준다.
  const [baeminPlatformId, setBaeminPlatformId] = useState<number | null>(null);

  useEffect(() => {
    apiGet<PlatformOption[]>("/platforms").then((rows) => {
      setBaeminPlatformId(rows.find((p) => p.code === "baemin")?.id ?? null);
    });
  }, []);

  useEffect(() => {
    if (!storeId || !baeminPlatformId) return;
    apiGet<DashboardResponse>(`/dashboard?store_id=${storeId}&platform_id=${baeminPlatformId}`).then(setDashboard);
    apiGet<Alert[]>(`/alerts?store_id=${storeId}`).then((a) => setAlerts(a.slice(0, 5)));
  }, [storeId, baeminPlatformId]);

  useEffect(() => {
    if (!storeId) return;
    apiGet<ShopBrand[]>(`/store-connections/baemin/shops?store_id=${storeId}`).then((b) => {
      setBrands(b);
      if (b.length > 0) setSelectedShopNo((prev) => prev || b[0].shop_no);
    });
  }, [storeId]);

  useEffect(() => {
    if (!storeId || !selectedShopNo) return;
    apiGet<ClickPerformance>(`/ads/click-performance?store_id=${storeId}&shop_no=${selectedShopNo}&days=14`).then(setClickPerf);
  }, [storeId, selectedShopNo]);

  useEffect(() => {
    if (!storeId || !baeminPlatformId) return;
    apiGet<SummaryResponse>(`/sales/summary?period=${period}&store_id=${storeId}&platform_id=${baeminPlatformId}`).then(setSales);
    apiGet<SummaryResponse>(`/deposits/summary?period=${period}&store_id=${storeId}&platform_id=${baeminPlatformId}`).then(setDeposits);
  }, [storeId, period, baeminPlatformId]);

  if (!dashboard || !storeId) return <p className="text-sm text-muted">불러오는 중...</p>;

  return (
    <div className="max-w-6xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{dashboard.store.name}</h1>
        <p className="text-sm text-muted">{dashboard.store.category} · 카드를 클릭하면 상세를 볼 수 있습니다 (Mock 데이터)</p>
      </div>

      <div className="flex items-center gap-2">
        {PERIODS.map((p) => (
          <button
            key={p.key}
            onClick={() => setPeriod(p.key)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              period === p.key ? "bg-accent text-white" : "border border-border-subtle text-muted hover:text-foreground"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-2xl border border-border-subtle bg-surface p-5">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="shrink-0 text-sm font-semibold text-foreground">우가클 점수</h2>
            {brands.length > 0 && (
              <select
                value={selectedShopNo}
                onChange={(e) => setSelectedShopNo(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="max-w-[140px] truncate rounded-lg border border-border-subtle bg-surface-2 px-2 py-1 text-xs outline-none focus:border-accent"
              >
                {brands.map((b) => (
                  <option key={b.shop_no} value={b.shop_no}>
                    {b.shop_name}
                  </option>
                ))}
              </select>
            )}
          </div>
          <button onClick={() => setOpenModal("ugacle")} className="w-full text-left">
            <p className="text-2xl font-bold text-accent">{clickPerf?.score ?? "—"}점</p>
            <p className="mt-1 text-xs text-muted">ACoS {clickPerf?.acos ?? "—"}%</p>
          </button>
        </div>
        <ClickableCard title="매출 분석" onClick={() => setOpenModal("sales_breakdown")}>
          <p className="text-2xl font-bold">{sales ? won(sales.total_sales ?? 0) : "…"}</p>
          <p className="mt-1 text-xs text-muted">정산표 · 손익 상세 보기</p>
        </ClickableCard>
        <ClickableCard title="재주문율 (보정 후)" onClick={() => setOpenModal("repurchase")}>
          <p className="text-2xl font-bold">
            {dashboard.repurchase_rate_adjusted !== null ? percent(dashboard.repurchase_rate_adjusted) : "—"}
          </p>
          <p className="mt-1 text-xs text-muted">최근 7일 합산 기준</p>
        </ClickableCard>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <ClickableCard title="매출" onClick={() => setOpenModal("sales_daily")}>
          <p className="text-2xl font-bold">{sales ? won(sales.total_sales ?? 0) : "…"}</p>
          <p className="mt-1 text-xs text-muted">{sales ? `${sales.from_date} ~ ${sales.to_date}` : ""}</p>
        </ClickableCard>
        <ClickableCard title="입금" onClick={() => setOpenModal("deposit_daily")}>
          <p className="text-2xl font-bold text-success">{deposits ? won(deposits.total_deposit ?? 0) : "…"}</p>
          <p className="mt-1 text-xs text-muted">정산 지연 반영 (D+3 가정)</p>
        </ClickableCard>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="답글 대기 리뷰">
          <div className="flex items-end justify-between">
            <p className="text-2xl font-bold text-warning">{dashboard.unanswered_reviews}건</p>
            <Link href="/reviews" className="text-xs text-accent hover:underline">리뷰 관리로 이동 →</Link>
          </div>
        </Card>
        <Card title={`알림 (${dashboard.unread_alerts}건 안읽음)`}>
          {alerts.length === 0 ? (
            <p className="text-sm text-muted">알림이 없습니다.</p>
          ) : (
            <ul className="space-y-2">
              {alerts.map((a) => {
                const meta = ALERT_LABEL[a.alert_type] ?? { label: a.alert_type, color: "text-muted" };
                return (
                  <li key={a.id} className="flex items-start gap-2 text-xs">
                    <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-medium ${meta.color} bg-surface-2`}>
                      {meta.label}
                    </span>
                    <span className="text-muted">{a.message}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>

      {openModal === "ugacle" && (
        <Modal title="우가클 점수" onClose={() => setOpenModal(null)}><UgacleModal storeId={storeId} shopNo={selectedShopNo} /></Modal>
      )}
      {openModal === "sales_breakdown" && (
        <Modal title="매출 분석" onClose={() => setOpenModal(null)}><SalesBreakdownModal storeId={storeId} period={period} /></Modal>
      )}
      {openModal === "repurchase" && (
        <Modal title="재주문율" onClose={() => setOpenModal(null)}><RepurchaseModal storeId={storeId} platformId={baeminPlatformId} /></Modal>
      )}
      {openModal === "sales_daily" && (
        <Modal title="일별 매출" onClose={() => setOpenModal(null)}><DailyAmountModal storeId={storeId} endpoint="/sales/daily" positive={false} platformId={baeminPlatformId} /></Modal>
      )}
      {openModal === "deposit_daily" && (
        <Modal title="일별 입금" onClose={() => setOpenModal(null)}><DailyAmountModal storeId={storeId} endpoint="/deposits/daily" positive={true} platformId={baeminPlatformId} /></Modal>
      )}
    </div>
  );
}
