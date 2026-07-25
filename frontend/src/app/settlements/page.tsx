"use client";
import { useCallback, useEffect, useState } from "react";
import { apiGet, won } from "@/lib/api";

type Row = {
  id: number; store_name: string; platform_name: string;
  period_start: string; period_end: string; payout_date: string;
  total_gross: number; total_deductions: number; net_payout: number; status: string;
};
type Detail = Row & {
  deductions_by_type: { type: string; amount: number }[];
  orders: { id: number; order_no: string; ordered_at: string; item_amount: number; delivery_tip: number; deduction_total: number }[];
};

const DEDUCTION_LABEL: Record<string, string> = {
  platform_commission: "중개수수료", payment_fee: "결제수수료",
  delivery_fee: "배달비", ad_fee: "광고비", discount_support: "할인지원",
};
const PLATFORM_OPTIONS = [
  { code: "", label: "전체 플랫폼" }, { code: "baemin", label: "배달의민족" },
  { code: "coupang_eats", label: "쿠팡이츠" }, { code: "yogiyo", label: "요기요" },
];

export default function SettlementsPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [platform, setPlatform] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);

  const load = useCallback(async () => {
    const qs = platform ? `?platform_code=${platform}` : "";
    setRows(await apiGet<Row[]>(`/api/settlements${qs}`));
    setDetail(null);
  }, [platform]);

  useEffect(() => { load(); }, [load]);

  return (
    <main className="mx-auto max-w-5xl p-8">
      <h1 className="text-xl font-bold">정산 차액 분해</h1>
      <select value={platform} onChange={(e) => setPlatform(e.target.value)} className="mt-3 rounded border px-2 py-1 text-sm">
        {PLATFORM_OPTIONS.map((p) => <option key={p.code} value={p.code}>{p.label}</option>)}
      </select>
      <div className="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2">
        <table className="w-full text-sm">
          <thead><tr className="border-b text-left text-gray-500">
            <th className="py-2">기간</th><th>매장/플랫폼</th><th className="text-right">실입금</th><th>상태</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} onClick={async () => setDetail(await apiGet<Detail>(`/api/settlements/${r.id}`))}
                className={`cursor-pointer border-b hover:bg-gray-50 ${detail?.id === r.id ? "bg-blue-50" : ""}`}>
                <td className="py-2">{r.period_start} ~ {r.period_end}</td>
                <td>{r.store_name}<span className="text-gray-400"> · {r.platform_name}</span></td>
                <td className="text-right font-medium">{won(r.net_payout)}</td>
                <td>{r.status === "paid" ? "입금완료" : "입금예정"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {detail && (
          <div className="rounded-lg border p-4 text-sm">
            <h2 className="font-semibold">{detail.period_start} ~ {detail.period_end} · {detail.platform_name}</h2>
            <dl className="mt-3 space-y-1">
              <div className="flex justify-between"><dt>주문 총액</dt><dd className="font-medium">{won(detail.total_gross)}</dd></div>
              {detail.deductions_by_type.map((d) => (
                <div key={d.type} className="flex justify-between text-red-600">
                  <dt>− {DEDUCTION_LABEL[d.type] ?? d.type}</dt><dd>−{won(d.amount)}</dd>
                </div>
              ))}
              <div className="flex justify-between border-t pt-1 text-base font-bold">
                <dt>실입금액</dt><dd>{won(detail.net_payout)}</dd>
              </div>
            </dl>
            <p className="mt-3 text-xs text-gray-400">주문 {detail.orders.length}건 · 입금일 {detail.payout_date}</p>
          </div>
        )}
      </div>
    </main>
  );
}
