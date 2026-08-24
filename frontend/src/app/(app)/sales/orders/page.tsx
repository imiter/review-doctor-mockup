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
type PlatformOption = { id: number; code: string; name: string; brand_color: string | null };

export default function OrdersPage() {
  const { storeId } = useStoreContext();
  const [orders, setOrders] = useState<Order[]>([]);
  const [baeminPlatformId, setBaeminPlatformId] = useState<number | null>(null);

  useEffect(() => {
    apiGet<PlatformOption[]>("/platforms").then((rows) => {
      setBaeminPlatformId(rows.find((p) => p.code === "baemin")?.id ?? null);
    });
  }, []);

  useEffect(() => {
    if (!storeId || !baeminPlatformId) return;
    apiGet<Order[]>(`/orders?store_id=${storeId}&platform_id=${baeminPlatformId}`).then(setOrders);
  }, [storeId, baeminPlatformId]);

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">주문내역</h1>
        <p className="text-sm text-muted">최근 30일 주문 내역입니다.</p>
      </div>

      {orders.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">주문 내역이 없습니다.</p>
        </Card>
      ) : (
        <div className="space-y-4">
          {orders.map((o) => (
            <div key={o.id} className="rounded-2xl border border-border-subtle bg-surface-2 p-6 shadow-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className="rounded-lg px-2.5 py-1 text-xs font-medium"
                  style={{ backgroundColor: `${o.platform_color}26`, color: o.platform_color ?? undefined }}
                >
                  {o.platform_name}
                </span>
                <span className="ml-auto text-xs text-muted">{new Date(o.ordered_at).toLocaleString("ko-KR")}</span>
              </div>
              <p className="mt-3 text-xs text-muted">{o.order_no}</p>

              <div className="mt-3 space-y-2 rounded-xl bg-surface p-4">
                <span className="inline-block rounded-lg bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent">
                  {o.order_type === "delivery" ? "배달" : "포장"}
                </span>
                <p className="text-sm leading-relaxed text-foreground">{o.menu_summary}</p>
              </div>

              <div className="mt-4 flex items-center justify-end gap-2">
                <span className="text-xs text-muted">주문금액</span>
                <span className="text-lg font-semibold text-foreground">{won(o.amount)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
