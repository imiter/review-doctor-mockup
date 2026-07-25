"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
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
  sales_today: number;
  deposit_today: number;
  unanswered_reviews: number;
  repurchase_rate_adjusted: number | null;
  ad_performance: { campaign_id: number; category: string; acos: number | null; score: number | null } | null;
  unread_alerts: number;
};
type Alert = { id: number; alert_type: string; message: string; created_at: string };

const ALERT_LABEL: Record<string, { label: string; color: string }> = {
  negative_review: { label: "부정 리뷰", color: "text-danger" },
  unanswered_review: { label: "미답변", color: "text-warning" },
  rank_drop: { label: "순위 하락", color: "text-danger" },
};

export default function DashboardPage() {
  const { storeId } = useStoreContext();
  const [period, setPeriod] = useState<Period>("week");
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [sales, setSales] = useState<SummaryResponse | null>(null);
  const [deposits, setDeposits] = useState<SummaryResponse | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    if (!storeId) return;
    apiGet<DashboardResponse>(`/dashboard?store_id=${storeId}`).then(setDashboard);
    apiGet<Alert[]>(`/alerts?store_id=${storeId}`).then((a) => setAlerts(a.slice(0, 5)));
  }, [storeId]);

  useEffect(() => {
    if (!storeId) return;
    apiGet<SummaryResponse>(`/sales/summary?period=${period}&store_id=${storeId}`).then(setSales);
    apiGet<SummaryResponse>(`/deposits/summary?period=${period}&store_id=${storeId}`).then(setDeposits);
  }, [storeId, period]);

  if (!dashboard) return <p className="text-sm text-muted">불러오는 중...</p>;

  return (
    <div className="max-w-6xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{dashboard.store.name}</h1>
        <p className="text-sm text-muted">{dashboard.store.category} · 대시보드 요약 (Mock 데이터)</p>
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

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card title="매출">
          <p className="text-2xl font-bold">{sales ? won(sales.total_sales ?? 0) : "…"}</p>
          <p className="mt-1 text-xs text-muted">{sales ? `${sales.from_date} ~ ${sales.to_date}` : ""}</p>
        </Card>
        <Card title="입금">
          <p className="text-2xl font-bold text-success">{deposits ? won(deposits.total_deposit ?? 0) : "…"}</p>
          <p className="mt-1 text-xs text-muted">정산 지연 반영 (D+3 가정)</p>
        </Card>
        <Card title="답글 대기 리뷰">
          <p className="text-2xl font-bold text-warning">{dashboard.unanswered_reviews}건</p>
          <p className="mt-1 text-xs text-muted">리뷰 관리에서 바로 답글 생성</p>
        </Card>
        <Card title="재주문율 (보정 후)">
          <p className="text-2xl font-bold">
            {dashboard.repurchase_rate_adjusted !== null ? percent(dashboard.repurchase_rate_adjusted) : "—"}
          </p>
          <p className="mt-1 text-xs text-muted">최근 7일 합산 기준</p>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="광고 성과 (ACoS)">
          {dashboard.ad_performance ? (
            <div className="flex items-end justify-between">
              <div>
                <p className="text-xs text-muted">{dashboard.ad_performance.category} 캠페인</p>
                <p className="mt-1 text-2xl font-bold">
                  {dashboard.ad_performance.acos !== null ? `${dashboard.ad_performance.acos}%` : "—"}
                </p>
              </div>
              <div className="text-right">
                <p className="text-xs text-muted">성과 점수</p>
                <p className="text-lg font-semibold text-accent">{dashboard.ad_performance.score ?? "—"}점</p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted">등록된 광고 캠페인이 없습니다.</p>
          )}
          <p className="mt-3 text-[11px] text-muted">ACoS = CPC ÷ (CVR × AOV) × 100 — 최근 14일 실계산</p>
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
    </div>
  );
}
