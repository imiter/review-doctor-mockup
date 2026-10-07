"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPatch } from "@/lib/api";
import { Toggle } from "@/components/Toggle";

type SyncStatus = "pending" | "running" | "success" | "failed";

type StoreRow = {
  store_id: number;
  store_name: string;
  owner_email: string | null;
  owner_nickname: string;
  last_sync: {
    triggered_by: "manual" | "scheduled";
    status: SyncStatus;
    started_at: string;
    finished_at: string | null;
    error_message: string | null;
  } | null;
  auto_reply_enabled: boolean;
};

const SYNC_STATUS_LABEL: Record<SyncStatus, { label: string; className: string }> = {
  pending: { label: "대기중", className: "text-muted" },
  running: { label: "진행중", className: "text-accent" },
  success: { label: "성공", className: "text-success" },
  failed: { label: "실패", className: "text-danger" },
};

export default function AdminStoresPage() {
  const [rows, setRows] = useState<StoreRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [togglingId, setTogglingId] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    apiGet<StoreRow[]>("/admin/stores")
      .then(setRows)
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handleToggle = async (storeId: number, next: boolean) => {
    setTogglingId(storeId);
    try {
      await apiPatch(`/admin/stores/${storeId}/auto-reply`, { enabled: next });
      setRows((prev) => prev.map((r) => (r.store_id === storeId ? { ...r, auto_reply_enabled: next } : r)));
    } catch {
      alert("변경에 실패했어요. 다시 시도해주세요.");
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">매장 운영 현황</h1>
      <p className="text-xs text-muted">배민 실계정이 연결된 매장만 표시됩니다.</p>

      {loading ? (
        <p className="text-sm text-muted">불러오는 중...</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">데이터가 없습니다.</p>
      ) : (
        <div className="space-y-3">
          {rows.map((r) => (
            <div key={r.store_id} className="rounded-2xl border border-border-subtle bg-surface p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold">{r.store_name}</p>
                  <p className="text-xs text-muted">{r.owner_nickname} · {r.owner_email ?? "카카오 계정"}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted">자동답글</span>
                  <Toggle
                    checked={r.auto_reply_enabled}
                    onChange={(next) => handleToggle(r.store_id, next)}
                    disabled={togglingId === r.store_id}
                  />
                </div>
              </div>

              <div className="mt-3 border-t border-border-subtle pt-3 text-xs">
                {r.last_sync === null ? (
                  <p className="text-muted">동기화 기록 없음</p>
                ) : (
                  <>
                    <p className={SYNC_STATUS_LABEL[r.last_sync.status].className}>
                      {SYNC_STATUS_LABEL[r.last_sync.status].label}
                      {" · "}
                      {r.last_sync.triggered_by === "manual" ? "수동" : "자동"}
                      {" · "}
                      {new Date(r.last_sync.finished_at ?? r.last_sync.started_at).toLocaleString("ko-KR")}
                    </p>
                    {r.last_sync.error_message && (
                      <p className="mt-1 text-danger">{r.last_sync.error_message}</p>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
