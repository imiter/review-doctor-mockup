"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { ApiError, apiGet, apiPost, won } from "@/lib/api";
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

type DistancePoint = {
  point_label: string;
  distance_km: number;
  current_rank: number;
  total_scanned: number;
  ads_above: number;
  snapshot_at: string;
};

type DistanceRankRow = {
  campaign_id: number;
  category: string;
  target_rank: number;
  points: DistancePoint[];
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

// 배포본(Railway)에는 로컬 에뮬레이터/Appium이 없어 실제 크롤링이 불가능하다 —
// 이 플래그가 "true"일 때만(로컬 개발) 버튼을 활성화한다. frontend/.env.local 참고.
const LIVE_CRAWL_ENABLED = process.env.NEXT_PUBLIC_LIVE_CRAWL_ENABLED === "true";

const ACTION_LABEL: Record<string, string> = {
  keep: "유지",
  raise_cpc: "CPC 인상 권장",
  lower_cpc: "CPC 인하 권장",
};

export default function AdsPage() {
  const { storeId } = useStoreContext();
  const [ranks, setRanks] = useState<RankRow[]>([]);
  const [distanceRanks, setDistanceRanks] = useState<DistanceRankRow[]>([]);
  const [performance, setPerformance] = useState<PerformanceRow[]>([]);
  const [runningCampaignId, setRunningCampaignId] = useState<number | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    if (!storeId) return;
    apiGet<RankRow[]>(`/ads/rank-monitoring?store_id=${storeId}`).then(setRanks);
    apiGet<DistanceRankRow[]>(`/ads/rank-by-distance?store_id=${storeId}`).then(setDistanceRanks);
    apiGet<PerformanceRow[]>(`/ads/performance?store_id=${storeId}&days=14`).then(setPerformance);
  }, [storeId]);

  async function handleRunCheck(campaignId: number) {
    setRunningCampaignId(campaignId);
    setRunError(null);
    try {
      const res = await apiPost<{ inserted: number; skipped: number; points: DistancePoint[] }>(
        `/ads/rank-by-distance/run?campaign_id=${campaignId}`
      );
      setDistanceRanks((prev) =>
        prev.map((c) => (c.campaign_id === campaignId ? { ...c, points: res.points } : c))
      );
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : "순위 확인 중 오류가 발생했습니다.");
    } finally {
      setRunningCampaignId(null);
    }
  }

  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">광고 순위 모니터링</h1>
        <p className="text-sm text-muted">
          순위 현황·경쟁 CPC는 수집됐다고 가정한 Mock 스냅샷입니다. 아래 반경별 순위만
          실기기 자동화로 실측한 값이며, 사이트가 요청마다 직접 크롤링하지는 않습니다.
          CPC 자동 입찰은 하지 않습니다.
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

      <Card title="반경별 실측 순위">
        <p className="mb-3 text-xs text-muted">
          가게 기준 거리(0km / 1.5~2.5km / 2.5~3.5km)에 따라 카테고리 순위가 어떻게
          달라지는지 실기기 자동화로 실측한 값입니다. &quot;우리가게 순위 확인&quot;을
          누르면 에뮬레이터가 실제로 배민 앱을 조작해 새로 측정합니다(지점당 약 1분,
          완료까지 수 분 소요).
        </p>
        {runError && (
          <p className="mb-3 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{runError}</p>
        )}
        <div className="space-y-4">
          {distanceRanks.map((c) => (
            <div key={c.campaign_id}>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-sm font-medium">{c.category}</p>
                <button
                  onClick={() => handleRunCheck(c.campaign_id)}
                  disabled={!LIVE_CRAWL_ENABLED || runningCampaignId !== null}
                  title={LIVE_CRAWL_ENABLED ? undefined : "로컬 데모 전용 — 실기기 에뮬레이터가 필요한 기능입니다"}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {!LIVE_CRAWL_ENABLED
                    ? "우리가게 순위 확인 (로컬 데모 전용)"
                    : runningCampaignId === c.campaign_id
                      ? "순위 확인 중… (수 분 소요)"
                      : "우리가게 순위 확인"}
                </button>
              </div>
              {c.points.length === 0 ? (
                <p className="text-sm text-muted">아직 실측 데이터가 없습니다.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-border-subtle text-xs text-muted">
                        <th className="py-2 font-medium">거리</th>
                        <th className="font-medium">순위</th>
                        <th className="font-medium">스캔 개수</th>
                        <th className="font-medium">위 광고 수</th>
                        <th className="font-medium">측정 시각</th>
                      </tr>
                    </thead>
                    <tbody>
                      {c.points.map((p) => (
                        <tr key={p.point_label} className="border-b border-border-subtle last:border-0">
                          <td className="py-3">{p.point_label}</td>
                          <td
                            className={`font-semibold ${
                              p.current_rank > c.target_rank ? "text-danger" : "text-success"
                            }`}
                          >
                            {p.current_rank}위
                          </td>
                          <td>{p.total_scanned}개</td>
                          <td>{p.ads_above}개</td>
                          <td className="text-xs text-muted">
                            {new Date(p.snapshot_at).toLocaleString("ko-KR")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
          {distanceRanks.length === 0 && (
            <p className="text-sm text-muted">등록된 광고 캠페인이 없습니다.</p>
          )}
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
