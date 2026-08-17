"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiPost, ApiError } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

export default function BillingSuccessPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { refreshBilling } = useStoreContext();
  const [state, setState] = useState<"loading" | "done" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const orderId = params.get("orderId");
    const paymentKey = params.get("paymentKey");
    const amount = params.get("amount");
    if (!orderId || !paymentKey || !amount) {
      setState("error");
      setMessage("결제 정보가 올바르지 않습니다.");
      return;
    }

    apiPost("/billing/confirm", { order_id: orderId, payment_key: paymentKey, amount: Number(amount) })
      .then(async () => {
        await refreshBilling();
        setState("done");
      })
      .catch((e) => {
        setState("error");
        setMessage(e instanceof ApiError ? e.message : "결제 승인 중 오류가 발생했습니다.");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="mx-auto max-w-md space-y-4 py-16 text-center">
      {state === "loading" && <p className="text-sm text-muted">결제를 확인하고 있어요...</p>}
      {state === "done" && (
        <>
          <p className="text-lg font-semibold">Pro 플랜이 시작됐어요!</p>
          <button
            onClick={() => router.push("/account/billing")}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
          >
            구독 관리로 돌아가기
          </button>
        </>
      )}
      {state === "error" && (
        <>
          <p className="text-sm text-danger">{message}</p>
          <button
            onClick={() => router.push("/account/billing")}
            className="rounded-lg border border-border-subtle px-4 py-2 text-sm"
          >
            구독 관리로 돌아가기
          </button>
        </>
      )}
    </div>
  );
}
