"use client";

import { useEffect, useState } from "react";
import { apiGet, won } from "@/lib/api";

type PaymentRow = {
  order_id: string;
  user_email: string | null;
  user_nickname: string;
  plan: string;
  amount: number;
  status: "pending" | "approved" | "failed";
  requested_at: string;
  approved_at: string | null;
  fail_reason: string | null;
};

const STATUS_LABEL: Record<PaymentRow["status"], { label: string; className: string }> = {
  pending: { label: "대기중", className: "text-warning" },
  approved: { label: "승인됨", className: "text-success" },
  failed: { label: "실패", className: "text-danger" },
};

export default function AdminPaymentsPage() {
  const [rows, setRows] = useState<PaymentRow[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const query = statusFilter ? `?status=${statusFilter}` : "";
    apiGet<PaymentRow[]>(`/admin/payments${query}`)
      .then(setRows)
      .finally(() => setLoading(false));
  }, [statusFilter]);

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">결제 이력</h1>

      <div className="flex gap-2">
        {["", "pending", "approved", "failed"].map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              statusFilter === s ? "bg-accent text-white" : "border border-border-subtle text-muted hover:text-foreground"
            }`}
          >
            {s === "" ? "전체" : STATUS_LABEL[s as PaymentRow["status"]].label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-sm text-muted">불러오는 중...</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">데이터가 없습니다.</p>
      ) : (
        <>
          {/* 좁은 화면에서는 표 대신 카드 목록으로 — 가로 스크롤 없이 한 화면 폭에 맞춘다 */}
          <div className="space-y-3 md:hidden">
            {rows.map((r) => (
              <div key={r.order_id} className="rounded-2xl border border-border-subtle bg-surface p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium">{r.user_nickname}</p>
                    <p className="text-xs text-muted">{r.user_email ?? "카카오 계정"}</p>
                  </div>
                  <span className={`text-xs font-medium ${STATUS_LABEL[r.status].className}`}>
                    {STATUS_LABEL[r.status].label}
                  </span>
                </div>
                <dl className="mt-3 space-y-1 text-xs text-muted">
                  <div className="flex justify-between"><dt>플랜</dt><dd className="text-foreground">{r.plan}</dd></div>
                  <div className="flex justify-between"><dt>금액</dt><dd className="text-foreground">{won(r.amount)}</dd></div>
                  <div className="flex justify-between"><dt>요청 시각</dt><dd>{new Date(r.requested_at).toLocaleString("ko-KR")}</dd></div>
                  {r.fail_reason && <div className="flex justify-between"><dt>실패 사유</dt><dd>{r.fail_reason}</dd></div>}
                </dl>
              </div>
            ))}
          </div>

          <div className="hidden overflow-x-auto rounded-2xl border border-border-subtle bg-surface md:block">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border-subtle text-xs text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">사용자</th>
                  <th className="px-4 py-3 font-medium">플랜</th>
                  <th className="px-4 py-3 font-medium">금액</th>
                  <th className="px-4 py-3 font-medium">상태</th>
                  <th className="px-4 py-3 font-medium">요청 시각</th>
                  <th className="px-4 py-3 font-medium">실패 사유</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.order_id} className="border-b border-border-subtle last:border-0">
                    <td className="px-4 py-3">
                      <p>{r.user_nickname}</p>
                      <p className="text-xs text-muted">{r.user_email ?? "카카오 계정"}</p>
                    </td>
                    <td className="px-4 py-3">{r.plan}</td>
                    <td className="px-4 py-3">{won(r.amount)}</td>
                    <td className={`px-4 py-3 font-medium ${STATUS_LABEL[r.status].className}`}>
                      {STATUS_LABEL[r.status].label}
                    </td>
                    <td className="px-4 py-3 text-xs text-muted">{new Date(r.requested_at).toLocaleString("ko-KR")}</td>
                    <td className="px-4 py-3 text-xs text-muted">{r.fail_reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
