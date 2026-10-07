"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPatch } from "@/lib/api";

type UserRow = {
  user_id: number;
  email: string | null;
  nickname: string;
  created_at: string;
  plan: "basic" | "pro";
  expires_at: string | null;
  store_count: number;
};

export default function AdminUsersPage() {
  const [rows, setRows] = useState<UserRow[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [daysInputs, setDaysInputs] = useState<Record<number, string>>({});
  const [savingId, setSavingId] = useState<number | null>(null);

  const load = (q: string) => {
    setLoading(true);
    const qs = q ? `?q=${encodeURIComponent(q)}` : "";
    apiGet<UserRow[]>(`/admin/users${qs}`)
      .then(setRows)
      .finally(() => setLoading(false));
  };

  useEffect(() => load(""), []);

  const changePlan = async (userId: number, plan: "basic" | "pro") => {
    setSavingId(userId);
    try {
      const days = plan === "pro" ? Number(daysInputs[userId] || "30") : undefined;
      const updated = await apiPatch<{ plan: "basic" | "pro"; expires_at: string | null }>(
        `/admin/users/${userId}/plan`,
        { plan, days },
      );
      setRows((prev) =>
        prev.map((r) => (r.user_id === userId ? { ...r, plan: updated.plan, expires_at: updated.expires_at } : r)),
      );
    } catch {
      alert("플랜 변경에 실패했어요. 다시 시도해주세요.");
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">유저 관리</h1>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          load(query);
        }}
        className="flex gap-2"
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="이메일 또는 닉네임 검색"
          className="w-full max-w-xs rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <button type="submit" className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
          검색
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-muted">불러오는 중...</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">데이터가 없습니다.</p>
      ) : (
        <div className="space-y-3">
          {rows.map((r) => (
            <div key={r.user_id} className="rounded-2xl border border-border-subtle bg-surface p-5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold">{r.nickname}</p>
                  <p className="text-xs text-muted">
                    {r.email ?? "카카오 계정"} · 가입일 {new Date(r.created_at).toLocaleDateString("ko-KR")} · 매장 {r.store_count}개
                  </p>
                </div>
                <div className="text-right">
                  <p className={`text-sm font-semibold ${r.plan === "pro" ? "text-accent" : "text-foreground"}`}>
                    {r.plan === "pro" ? "Pro" : "Basic"}
                  </p>
                  {r.expires_at && <p className="text-xs text-muted">~{r.expires_at}</p>}
                </div>
              </div>

              <div className="mt-3 flex items-center gap-2 border-t border-border-subtle pt-3">
                <input
                  type="number"
                  min={1}
                  max={365}
                  placeholder="30"
                  value={daysInputs[r.user_id] ?? ""}
                  onChange={(e) => setDaysInputs((prev) => ({ ...prev, [r.user_id]: e.target.value }))}
                  className="w-20 rounded-lg border border-border-subtle bg-surface-2 px-2 py-1.5 text-xs outline-none focus:border-accent"
                />
                <span className="text-xs text-muted">일간 Pro 부여</span>
                <button
                  onClick={() => {
                    const days = daysInputs[r.user_id] || "30";
                    if (!confirm(`${r.nickname}님에게 Pro ${days}일을 부여할까요? (기존 만료일이 남아있으면 그 날짜부터 연장됩니다)`)) return;
                    changePlan(r.user_id, "pro");
                  }}
                  disabled={savingId === r.user_id}
                  className="rounded-lg border border-accent px-3 py-1.5 text-xs text-accent transition hover:bg-accent-soft disabled:opacity-50"
                >
                  Pro로 변경
                </button>
                <button
                  onClick={() => {
                    if (!confirm(`${r.nickname}님을 Basic으로 변경할까요? 구독이 즉시 만료됩니다.`)) return;
                    changePlan(r.user_id, "basic");
                  }}
                  disabled={savingId === r.user_id}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                >
                  Basic으로 변경
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
