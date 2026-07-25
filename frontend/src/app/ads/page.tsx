"use client";
import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, won } from "@/lib/api";

type Rec = { id: number; action_type: string; suggested_cpc: number };
type Campaign = {
  id: number; store_name: string; platform_name: string; category: string;
  current_cpc: number; target_rank: number; my_rank: number | null;
  competitor_est_cpc: number | null; status: string; recommendation: Rec | null;
};

export default function AdsPage() {
  const [mockNow, setMockNow] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);

  const load = useCallback(async () => {
    try {
      const body = await apiGet<{ mock_now: string; campaigns: Campaign[] }>("/api/ad-campaigns");
      setMockNow(body.mock_now);
      setCampaigns(body.campaigns);
    } catch (e) {
      console.error(e);
      setMockNow("");
      setCampaigns([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const refresh = async () => {
    try {
      await apiPost("/api/ads/refresh");
      await load();
    } catch (e) {
      console.error(e);
    }
  };

  const act = async (recId: number, action: "apply" | "dismiss") => {
    try {
      await apiPost(`/api/ad-recommendations/${recId}/${action}`);
      await load();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <main className="mx-auto max-w-5xl p-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">광고 순위 모니터링</h1>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-gray-500">기준 시각 {mockNow.replace("T", " ")}</span>
          <button onClick={refresh} className="rounded bg-black px-3 py-1 text-white">새로고침 (+10분)</button>
        </div>
      </div>
      <table className="mt-4 w-full text-sm">
        <thead><tr className="border-b text-left text-gray-500">
          <th className="py-2">매장/플랫폼</th><th>카테고리</th><th className="text-right">현재 CPC</th>
          <th className="text-center">목표 순위</th><th className="text-center">현재 순위</th>
          <th className="text-right">경쟁 예상 CPC</th><th>상태</th><th>추천 액션</th>
        </tr></thead>
        <tbody>
          {campaigns.map((c) => {
            const slipped = c.my_rank !== null && c.my_rank > c.target_rank;
            return (
              <tr key={c.id} className="border-b">
                <td className="py-2">{c.store_name}<span className="text-gray-400"> · {c.platform_name}</span></td>
                <td>{c.category}</td>
                <td className="text-right">{won(c.current_cpc)}</td>
                <td className="text-center">{c.target_rank}위</td>
                <td className={`text-center font-bold ${slipped ? "text-red-600" : "text-green-600"}`}>
                  {c.my_rank === null ? "—" : `${c.my_rank}위`}
                </td>
                <td className="text-right">{c.competitor_est_cpc === null ? "—" : won(c.competitor_est_cpc)}</td>
                <td>{slipped ? "순위 밀림" : c.status === "active" ? "정상" : "일시정지"}</td>
                <td>
                  {c.recommendation ? (
                    <div className="flex items-center gap-2">
                      <span>CPC {won(c.recommendation.suggested_cpc)}로 인상</span>
                      <button onClick={() => act(c.recommendation!.id, "apply")} className="rounded bg-blue-600 px-2 py-0.5 text-xs text-white">적용</button>
                      <button onClick={() => act(c.recommendation!.id, "dismiss")} className="rounded border px-2 py-0.5 text-xs">무시</button>
                    </div>
                  ) : <span className="text-gray-400">유지</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-gray-400">
        순위·경쟁 CPC는 seed된 10분 간격 스냅샷이며, 새로고침마다 mock 시간이 전진합니다. 실제 크롤링/자동입찰 없음.
      </p>
    </main>
  );
}
