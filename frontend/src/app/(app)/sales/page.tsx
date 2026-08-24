"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, won } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type DailyRow = { date: string; amount: number };
type PlatformOption = { id: number; code: string; name: string };

const DAY_LABEL = (iso: string, todayIso: string, yesterdayIso: string) => {
  const d = new Date(iso);
  const weekday = ["일", "월", "화", "수", "목", "금", "토"][d.getDay()];
  if (iso === todayIso) return `오늘 (${d.getMonth() + 1}/${d.getDate()}, ${weekday})`;
  if (iso === yesterdayIso) return `어제 (${d.getMonth() + 1}/${d.getDate()}, ${weekday})`;
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${weekday})`;
};

export default function SalesDailyPage() {
  const { storeId } = useStoreContext();
  const [rows, setRows] = useState<DailyRow[]>([]);
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [platformId, setPlatformId] = useState<number | "">("");
  const [view, setView] = useState<"list" | "graph">("list");

  useEffect(() => {
    apiGet<PlatformOption[]>("/platforms").then(setPlatforms);
  }, []);

  useEffect(() => {
    if (!storeId) return;
    const qs = platformId ? `&platform_id=${platformId}` : "";
    apiGet<DailyRow[]>(`/sales/daily?store_id=${storeId}&days=14${qs}`).then((r) => setRows([...r].reverse()));
  }, [storeId, platformId]);

  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const max = Math.max(1, ...rows.map((r) => r.amount));

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">매출</h1>
        <p className="text-sm text-muted">연결된 매장의 날짜별 매출 데이터를 확인할 수 있습니다.</p>
      </div>

      <div className="flex items-center justify-between">
        <select
          value={platformId}
          onChange={(e) => setPlatformId(e.target.value ? Number(e.target.value) : "")}
          className="rounded-lg border border-border-subtle bg-surface-2 px-3 py-1.5 text-xs outline-none focus:border-accent"
        >
          <option value="">전체 플랫폼</option>
          {platforms.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <div className="flex gap-1 rounded-lg border border-border-subtle p-1">
          {(["list", "graph"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`rounded px-3 py-1 text-xs font-medium transition ${view === v ? "bg-accent text-white" : "text-muted"}`}
            >
              {v === "list" ? "리스트" : "그래프"}
            </button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">데이터가 없습니다.</p>
        </Card>
      ) : view === "list" ? (
        <div className="space-y-3">
          {rows.map((r) => (
            <div
              key={r.date}
              className="flex items-center justify-between rounded-2xl border border-border-subtle bg-surface-2 px-6 py-4 shadow-sm"
            >
              <span className="text-sm font-medium text-foreground">{DAY_LABEL(r.date, today, yesterday)}</span>
              <span className="text-lg font-semibold text-accent">{won(r.amount)}</span>
            </div>
          ))}
        </div>
      ) : (
        <Card>
          <div className="space-y-2">
            {rows.map((r) => (
              <div key={r.date} className="flex items-center gap-3">
                <span className="w-24 shrink-0 text-xs text-muted">{r.date.slice(5)}</span>
                <div className="h-3 flex-1 overflow-hidden rounded-full bg-surface-2">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${(r.amount / max) * 100}%` }} />
                </div>
                <span className="w-24 shrink-0 text-right text-xs">{won(r.amount)}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
