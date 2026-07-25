"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, won } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

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

type PerformanceRow = {
  campaign_id: number;
  category: string;
  ad_spend: number;
  clicks: number;
  ad_orders: number;
  ad_revenue: number;
  cpc: number;
  cvr: number;
  aov: number;
  acos: number | null;
  score: number | null;
};

const ACTION_LABEL: Record<string, string> = {
  keep: "유지",
  raise_cpc: "CPC 인상 권장",
  lower_cpc: "CPC 인하 권장",
};

export default function AdsPage() {
  const { storeId } = useStoreContext();
  const [ranks, setRanks] = useState<RankRow[]>([]);
  const [performance, setPerformance] = useState<PerformanceRow[]>([]);

  useEffect(() => {
    if (!storeId) return;
    apiGet<RankRow[]>(`/ads/rank-monitoring?store_id=${storeId}`).then(setRanks);
    apiGet<PerformanceRow[]>(`/ads/performance?store_id=${storeId}&days=14`).then(setPerformance);
  }, [storeId]);

  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">광고 순위 모니터링</h1>
        <p className="text-sm text-muted">
          순위·경쟁 CPC는 수집됐다고 가정한 Mock 스냅샷입니다. 실제 크롤링·자동 입찰은 하지 않습니다.
        </p>
      </div>

      <Card title="순위 현황">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border-subtle text-xs text-muted">
                <th className="py-2 font-medium">카테고리</th>
                <th className="font-medium">현재 CPC</th>
                <th className="font-medium">목표 순위</th>
                <th className="font-medium">현재 순위</th>
                <th className="font-medium">경쟁 예상 CPC</th>
                <th className="font-medium">상태</th>
                <th className="font-medium">추천 액션</th>
              </tr>
            </thead>
            <tbody>
              {ranks.map((r) => {
                const dropped = r.rank_status === "rank_dropped";
                return (
                  <tr key={r.campaign_id} className="border-b border-border-subtle last:border-0">
                    <td className="py-3">{r.category}</td>
                    <td>{won(r.current_cpc)}</td>
                    <td>{r.target_rank}위</td>
                    <td className={`font-semibold ${dropped ? "text-danger" : "text-success"}`}>
                      {r.current_rank === null ? "—" : `${r.current_rank}위`}
                    </td>
                    <td>{r.competitor_est_cpc === null ? "—" : won(r.competitor_est_cpc)}</td>
                    <td>
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${
                          dropped ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent"
                        }`}
                      >
                        {dropped ? "순위 밀림" : r.status === "active" ? "정상" : "일시정지"}
                      </span>
                    </td>
                    <td className="text-xs text-muted">
                      {ACTION_LABEL[r.recommended_action]}
                      {r.suggested_cpc && ` (${won(r.suggested_cpc)})`}
                    </td>
                  </tr>
                );
              })}
              {ranks.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-sm text-muted">등록된 광고 캠페인이 없습니다.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="광고 성과 (최근 14일, ACoS 실계산)">
        <div className="grid gap-4 sm:grid-cols-2">
          {performance.map((p) => (
            <div key={p.campaign_id} className="rounded-xl border border-border-subtle bg-surface-2 p-4">
              <p className="text-sm font-medium">{p.category}</p>
              <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
                <div>
                  <p className="text-muted">CPC</p>
                  <p className="font-semibold">{won(Math.round(p.cpc))}</p>
                </div>
                <div>
                  <p className="text-muted">CVR</p>
                  <p className="font-semibold">{(p.cvr * 100).toFixed(1)}%</p>
                </div>
                <div>
                  <p className="text-muted">AOV</p>
                  <p className="font-semibold">{won(Math.round(p.aov))}</p>
                </div>
                <div>
                  <p className="text-muted">ACoS</p>
                  <p className="font-semibold">{p.acos !== null ? `${p.acos}%` : "—"}</p>
                </div>
              </div>
              <div className="mt-3 flex items-center justify-between border-t border-border-subtle pt-3">
                <span className="text-xs text-muted">성과 점수</span>
                <span className="text-lg font-bold text-accent">{p.score ?? "—"}점</span>
              </div>
            </div>
          ))}
          {performance.length === 0 && <p className="text-sm text-muted">데이터가 없습니다.</p>}
        </div>
      </Card>
    </div>
  );
}
