"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, won } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type Order = {
  id: number;
  order_no: string;
  platform_name: string;
  platform_color: string | null;
  ordered_at: string;
  menu_summary: string;
  order_type: string;
  amount: number;
};

export default function OrdersPage() {
  const { storeId } = useStoreContext();
  const [orders, setOrders] = useState<Order[]>([]);

  useEffect(() => {
    if (!storeId) return;
    apiGet<Order[]>(`/orders?store_id=${storeId}`).then(setOrders);
  }, [storeId]);

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">주문내역</h1>
        <p className="text-sm text-muted">최근 60일 주문 내역입니다.</p>
      </div>

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border-subtle text-xs text-muted">
                <th className="py-2 font-medium">플랫폼</th>
                <th className="font-medium">주문시각</th>
                <th className="font-medium">주문번호</th>
                <th className="font-medium">주문내역</th>
                <th className="font-medium">주문유형</th>
                <th className="text-right font-medium">주문금액</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id} className="border-b border-border-subtle last:border-0">
                  <td className="py-3">
                    <span
                      className="rounded px-2 py-0.5 text-xs font-medium"
                      style={{ backgroundColor: `${o.platform_color}26`, color: o.platform_color ?? undefined }}
                    >
                      {o.platform_name}
                    </span>
                  </td>
                  <td className="text-xs text-muted">{new Date(o.ordered_at).toLocaleString("ko-KR")}</td>
                  <td className="text-xs text-muted">{o.order_no}</td>
                  <td>{o.menu_summary}</td>
                  <td className="text-xs text-muted">{o.order_type === "delivery" ? "배달" : "포장"}</td>
                  <td className="text-right font-medium">{won(o.amount)}</td>
                </tr>
              ))}
              {orders.length === 0 && (
                <tr><td colSpan={6} className="py-6 text-center text-muted">주문 내역이 없습니다.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
