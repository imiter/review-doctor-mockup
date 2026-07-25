"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiDelete, apiGet, apiPost, ApiError } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type Connection = {
  id: number;
  platform_id: number;
  platform_code: string;
  platform_name: string;
  brand_color: string | null;
  platform_store_id: string;
  business_number: string;
  connected_at: string;
};
type PlatformOption = { id: number; code: string; name: string; brand_color: string | null };

export default function StoreConnectionsPage() {
  const { storeId } = useStoreContext();
  const [connections, setConnections] = useState<Connection[]>([]);
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!storeId) return;
    setConnections(await apiGet<Connection[]>(`/store-connections?store_id=${storeId}`));
  }, [storeId]);

  useEffect(() => {
    apiGet<PlatformOption[]>("/platforms").then(setPlatforms);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const connectedIds = new Set(connections.map((c) => c.platform_id));
  const available = platforms.filter((p) => !connectedIds.has(p.id));

  const connect = async (platformId: number) => {
    setError(null);
    try {
      await apiPost("/store-connections", { platform_id: platformId, store_id: storeId });
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "연결에 실패했습니다");
    }
  };

  const disconnect = async (id: number) => {
    await apiDelete(`/store-connections/${id}`);
    load();
  };

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">가게 연결</h1>
        <p className="text-sm text-muted">
          매장 {connections.length}개 플랫폼에 연결되었습니다. 실제 배달앱 계정 연동은 하지 않으며
          연결하면 Mock 스토어 아이디가 즉석에서 생성됩니다.
        </p>
      </div>

      {available.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {available.map((p) => (
            <button
              key={p.id}
              onClick={() => connect(p.id)}
              className="rounded-lg border border-accent px-4 py-2 text-sm font-medium text-accent transition hover:bg-accent-soft"
            >
              + {p.name} 연결하기
            </button>
          ))}
        </div>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {connections.map((c) => (
          <Card key={c.id}>
            <div className="flex items-start justify-between">
              <div>
                <span
                  className="rounded px-2 py-0.5 text-xs font-medium"
                  style={{ backgroundColor: `${c.brand_color}26`, color: c.brand_color ?? undefined }}
                >
                  {c.platform_name}
                </span>
                <p className="mt-2 text-xs text-muted">스토어 아이디: {c.platform_store_id}</p>
                <p className="text-xs text-muted">사업자번호: {c.business_number}</p>
              </div>
              <button
                onClick={() => disconnect(c.id)}
                className="rounded-lg border border-danger/40 px-3 py-1 text-xs text-danger transition hover:bg-danger-soft"
              >
                연결 해제
              </button>
            </div>
          </Card>
        ))}
        {connections.length === 0 && <p className="text-sm text-muted">연결된 플랫폼이 없습니다.</p>}
      </div>
    </div>
  );
}
