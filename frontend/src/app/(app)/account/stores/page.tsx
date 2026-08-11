"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Card } from "@/components/Card";
import { Modal } from "@/components/Modal";
import { apiDelete, apiGet, apiPost, ApiError } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type Connection = {
  id: number;
  platform_id: number;
  platform_code: string;
  platform_name: string;
  brand_color: string | null;
  platform_store_id: string;
  business_number: string | null;
  has_real_credential: boolean;
  connected_at: string;
};
type PlatformOption = { id: number; code: string; name: string; brand_color: string | null };
type SyncStatus = {
  id: number;
  status: "pending" | "running" | "success" | "failed";
  reviews_fetched: number | null;
  reviews_inserted: number | null;
  error_message: string | null;
};

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

  const [loginTarget, setLoginTarget] = useState<PlatformOption | null>(null);
  const [loginId, setLoginId] = useState("");
  const [loginPw, setLoginPw] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  const openLogin = (p: PlatformOption) => {
    setLoginTarget(p);
    setLoginId("");
    setLoginPw("");
    setModalError(null);
  };
  const closeLogin = () => {
    if (connecting) return;
    setLoginTarget(null);
    setLoginId("");
    setLoginPw("");
  };

  const submitLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!loginTarget) return;
    setConnecting(true);
    setModalError(null);
    try {
      if (loginTarget.code === "baemin") {
        await apiPost(`/store-connections/baemin/login?store_id=${storeId}`, {
          platform_login_id: loginId,
          platform_login_password: loginPw,
        });
      } else {
        // Mock 로그인 — 입력한 아이디/비밀번호는 전송/저장되지 않는다. 실제 로그인 흐름처럼
        // 잠깐의 지연을 준 뒤 매장을 연결한다.
        await new Promise((r) => setTimeout(r, 700));
        await apiPost("/store-connections", { platform_id: loginTarget.id, store_id: storeId });
      }
      setLoginTarget(null);
      setLoginId("");
      setLoginPw("");
      load();
    } catch (e) {
      setModalError(e instanceof ApiError ? e.message : "연결에 실패했습니다");
    } finally {
      setConnecting(false);
    }
  };

  const disconnect = async (id: number) => {
    await apiDelete(`/store-connections/${id}`);
    load();
  };

  const [syncingId, setSyncingId] = useState<number | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startSync = async (connectionId: number) => {
    setSyncingId(connectionId);
    setSyncStatus(null);
    try {
      const { job_id } = await apiPost<{ job_id: number }>(
        `/store-connections/baemin/sync-reviews?store_id=${storeId}`
      );
      pollRef.current = setInterval(async () => {
        const status = await apiGet<SyncStatus>(`/store-connections/baemin/sync-status/${job_id}`);
        setSyncStatus(status);
        if (status.status === "success" || status.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          setSyncingId(null);
        }
      }, 4000);
    } catch (e) {
      setSyncStatus({
        id: 0, status: "failed", reviews_fetched: null, reviews_inserted: null,
        error_message: e instanceof ApiError ? e.message : "동기화 시작에 실패했습니다",
      });
      setSyncingId(null);
    }
  };

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">가게 연결</h1>
        <p className="text-sm text-muted">
          매장 {connections.length}개 플랫폼에 연결되었습니다. 배민은 실제 사장님광장 계정으로 로그인해
          리뷰를 가져올 수 있습니다. 그 외 플랫폼은 Mock 연동이며 실제 계정 연동은 하지 않습니다.
        </p>
      </div>

      {available.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {available.map((p) => (
            <button
              key={p.id}
              onClick={() => openLogin(p)}
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
                {c.business_number && <p className="text-xs text-muted">사업자번호: {c.business_number}</p>}
                {c.platform_code === "baemin" && !c.has_real_credential && (
                  <p className="mt-1 text-xs text-warning">
                    Mock 연결입니다. 실제 로그인하려면 연결 해제 후 다시 연결하세요.
                  </p>
                )}
              </div>
              <button
                onClick={() => disconnect(c.id)}
                className="rounded-lg border border-danger/40 px-3 py-1 text-xs text-danger transition hover:bg-danger-soft"
              >
                연결 해제
              </button>
            </div>

            {c.platform_code === "baemin" && c.has_real_credential && (
              <div className="mt-3 border-t border-border-subtle pt-3">
                <button
                  onClick={() => startSync(c.id)}
                  disabled={syncingId === c.id}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {syncingId === c.id ? "동기화 중..." : "데이터 동기화"}
                </button>
                {syncStatus && (
                  <p className="mt-2 text-xs text-muted">
                    {syncStatus.status === "success" &&
                      `${syncStatus.reviews_fetched}개 중 ${syncStatus.reviews_inserted}개 신규 추가`}
                    {syncStatus.status === "failed" && (
                      <span className="text-danger">동기화 실패: {syncStatus.error_message}</span>
                    )}
                    {(syncStatus.status === "pending" || syncStatus.status === "running") && "진행 중..."}
                  </p>
                )}
              </div>
            )}
          </Card>
        ))}
        {connections.length === 0 && <p className="text-sm text-muted">연결된 플랫폼이 없습니다.</p>}
      </div>

      {loginTarget && (
        <Modal title={`${loginTarget.name} 사장님광장 로그인`} onClose={closeLogin}>
          <form onSubmit={submitLogin} className="space-y-4">
            <p className="text-xs text-muted">
              {loginTarget.code === "baemin"
                ? "배민 사장님광장 아이디로 실제 로그인합니다. 로그인에 성공하면 리뷰를 실제로 가져올 수 있습니다."
                : `${loginTarget.name} 사장님광장 아이디로 로그인하면 매장이 연결됩니다. (Mock — 입력한 아이디/비밀번호는 서버로 전송되거나 저장되지 않습니다)`}
            </p>
            <div>
              <label className="mb-1 block text-xs text-muted">아이디</label>
              <input
                value={loginId}
                onChange={(e) => setLoginId(e.target.value)}
                required
                autoFocus
                disabled={connecting}
                placeholder="사장님광장 아이디"
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">비밀번호</label>
              <input
                type="password"
                value={loginPw}
                onChange={(e) => setLoginPw(e.target.value)}
                required
                disabled={connecting}
                placeholder="비밀번호"
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
              />
            </div>
            {modalError && <p className="text-xs text-danger">{modalError}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={closeLogin}
                disabled={connecting}
                className="rounded-lg px-4 py-2 text-sm text-muted transition hover:bg-surface-2 disabled:opacity-60"
              >
                취소
              </button>
              <button
                type="submit"
                disabled={connecting}
                className="rounded-lg px-4 py-2 text-sm font-medium text-white transition disabled:opacity-60"
                style={{ backgroundColor: loginTarget.brand_color ?? "#6d5ef5" }}
              >
                {connecting ? "연결 중..." : "로그인"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
