"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card } from "@/components/Card";
import { ApiError, apiGet, apiPatch, apiPost, won } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type RankRow = {
  campaign_id: number;
  category: string;
  display_name: string | null;
  current_cpc: number;
  target_rank: number;
  status: "active" | "paused";
  current_rank: number | null;
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
  display_name: string | null;
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

// 백엔드가 로컬 crawler venv 또는 CRAWL_WORKER_URL(터널로 노출한 워커) 중
// 하나로 실제 크롤링을 실행할 수 있을 때만 이 플래그를 켠다(로컬은
// frontend/.env.local, 배포본은 Railway 환경변수). 워커 컴퓨터가 꺼져있으면
// 버튼은 눌리지만 요청이 실패할 수 있다 — 그 경우 에러 메시지로 안내한다.
const LIVE_CRAWL_ENABLED = process.env.NEXT_PUBLIC_LIVE_CRAWL_ENABLED === "true";

const ACTION_LABEL: Record<string, string> = {
  keep: "유지",
  raise_cpc: "CPC 인상 권장",
  lower_cpc: "CPC 인하 권장",
};

export default function AdsPage() {
  const { storeId, billing } = useStoreContext();
  const [ranks, setRanks] = useState<RankRow[]>([]);
  const [distanceRanks, setDistanceRanks] = useState<DistanceRankRow[]>([]);
  const [performance, setPerformance] = useState<PerformanceRow[]>([]);
  const [runningCampaignId, setRunningCampaignId] = useState<number | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [bidInputs, setBidInputs] = useState<Record<number, string>>({});
  const [targetRankInputs, setTargetRankInputs] = useState<Record<number, string>>({});

  useEffect(() => {
    if (!storeId || (billing && !billing.is_pro)) return;
    // 백엔드도 이 라우트들을 Pro 전용으로 막는다(403). billing 상태가 아직 로딩
    // 중이거나 낡은 값일 때 요청이 나가 403을 받을 수 있어, unhandled promise
    // rejection으로 화면이 깨지지 않도록 조용히 무시한다.
    apiGet<RankRow[]>(`/ads/rank-monitoring?store_id=${storeId}`).then(setRanks).catch(() => {});
    apiGet<DistanceRankRow[]>(`/ads/rank-by-distance?store_id=${storeId}`).then(setDistanceRanks).catch(() => {});
    apiGet<PerformanceRow[]>(`/ads/performance?store_id=${storeId}&days=14`).then(setPerformance).catch(() => {});
  }, [storeId, billing]);

  // 크롤은 3~5분 걸리는데, 배포 환경(Railway) 앞단 프록시가 오래 걸리는 요청을
  // 강제로 끊어버려서(524 Timeout, 실측으로 확인) 응답 하나로 끝까지 기다릴 수
  // 없다 — 대신 시작만 요청하고(POST), 5초 간격으로 상태를 조회(GET)한다.
  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

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

  if (billing && !billing.is_pro) {
    return (
      <div className="mx-auto max-w-md space-y-4 py-24 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent">
          🔒
        </div>
        <p className="text-lg font-semibold">Pro 전용 기능입니다</p>
        <p className="text-sm text-muted">광고 순위 모니터링은 Pro 플랜에서 이용할 수 있어요.</p>
        <Link
          href="/account/billing"
          className="inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          Pro 시작하기
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-5xl space-y-6">
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

      <Card title="순위 현황">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border-subtle text-xs text-muted">
                <th className="py-2 font-medium">카테고리</th>
                <th className="font-medium">현재 CPC</th>
                <th className="font-medium">목표 순위</th>
                <th className="font-medium">현재 순위</th>
                <th className="font-medium">상태</th>
                <th className="font-medium">추천 액션</th>
              </tr>
            </thead>
            <tbody>
              {ranks.map((r) => {
                const dropped = r.rank_status === "rank_dropped";
                return (
                  <tr key={r.campaign_id} className="border-b border-border-subtle last:border-0">
                    <td className="py-3">{r.display_name ? `${r.display_name} · ${r.category}` : r.category}</td>
                    <td>{won(r.current_cpc)}</td>
                    <td>{r.target_rank}위</td>
                    <td className={`font-semibold ${dropped ? "text-danger" : "text-success"}`}>
                      {r.current_rank === null ? "—" : `${r.current_rank}위`}
                    </td>
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
                  <td colSpan={6} className="py-6 text-center text-sm text-muted">등록된 광고 캠페인이 없습니다.</td>
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
          {distanceRanks.map((c) => {
            const rank = ranks.find((r) => r.campaign_id === c.campaign_id);
            const bidValue = bidInputs[c.campaign_id] ?? (rank ? String(rank.current_cpc) : "");
            const targetRankValue = targetRankInputs[c.campaign_id] ?? String(c.target_rank);
            return (
            <div key={c.campaign_id}>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium">
                  {rank?.display_name ? `${rank.display_name} · ${c.category}` : c.category}
                </p>
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
                  disabled={!LIVE_CRAWL_ENABLED || runningCampaignId !== null}
                  title={LIVE_CRAWL_ENABLED ? undefined : "이 환경에서는 실측 크롤링을 실행할 수 없습니다"}
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
            );
          })}
          {distanceRanks.length === 0 && (
            <p className="text-sm text-muted">등록된 광고 캠페인이 없습니다.</p>
          )}
        </div>
      </Card>

      <Card title="광고 성과 (최근 14일, ACoS 실계산)">
        <div className="grid gap-4 sm:grid-cols-2">
          {performance.map((p) => (
            <div key={p.campaign_id} className="rounded-xl border border-border-subtle bg-surface-2 p-4">
              <p className="text-sm font-medium">
                {p.display_name ? `${p.display_name} · ${p.category}` : p.category}
              </p>
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
