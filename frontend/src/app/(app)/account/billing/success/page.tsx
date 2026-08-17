"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiPost, ApiError } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type ConfirmResult = {
  status: string;
  bank_code?: string | null;
  account_number?: string | null;
  due_date?: string | null;
};

export default function BillingSuccessPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { refreshBilling, billing } = useStoreContext();
  const [state, setState] = useState<"loading" | "done" | "error" | "waiting">("loading");
  const [message, setMessage] = useState("");
  const [bankInfo, setBankInfo] = useState<{ bankCode: string; accountNumber: string; dueDate: string } | null>(null);

  useEffect(() => {
    const orderId = params.get("orderId");
    const paymentKey = params.get("paymentKey");
    const amount = params.get("amount");
    if (!orderId || !paymentKey || !amount) {
      setState("error");
      setMessage("결제 정보가 올바르지 않습니다.");
      return;
    }

    // StrictMode의 effect 이중 마운트(dev 환경) 또는 컴포넌트 재실행 시, 먼저 날아간
    // 요청의 응답이 나중에 도착해 이미 확정된 화면 상태를 덮어쓰지 않도록 가드한다
    // (billing/page.tsx의 cancelled 컨벤션과 동일).
    let cancelled = false;

    apiPost<ConfirmResult>("/billing/confirm", { order_id: orderId, payment_key: paymentKey, amount: Number(amount) })
      .then(async (result) => {
        if (cancelled) return;
        if (result.status === "waiting_for_deposit") {
          setBankInfo({
            bankCode: result.bank_code ?? "",
            accountNumber: result.account_number ?? "",
            dueDate: result.due_date ?? "",
          });
          setState("waiting");
          return;
        }
        await refreshBilling();
        if (cancelled) return;
        setState("done");
      })
      .catch((e) => {
        if (cancelled) return;
        setState("error");
        setMessage(e instanceof ApiError ? e.message : "결제 승인 중 오류가 발생했습니다.");
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 대기 화면에서 "새로고침"으로 refreshBilling()을 다시 부르면 billing 컨텍스트가
  // 갱신된다 — 그 사이 웹훅이 도착해서 is_pro가 true가 됐으면 완료 화면으로 넘어간다.
  useEffect(() => {
    if (state === "waiting" && billing?.is_pro) {
      setState("done");
    }
  }, [state, billing]);

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
      {state === "waiting" && bankInfo && (
        <>
          <p className="text-lg font-semibold">가상계좌가 발급됐어요</p>
          <div className="rounded-lg border border-border-subtle p-4 text-left text-sm">
            <p className="text-muted">은행 코드: {bankInfo.bankCode}</p>
            <p className="text-muted">계좌번호: {bankInfo.accountNumber}</p>
            {bankInfo.dueDate && <p className="text-muted">입금기한: {bankInfo.dueDate}</p>}
          </div>
          <p className="text-sm text-muted">입금이 완료되면 자동으로 Pro로 전환돼요.</p>
          <button
            onClick={() => refreshBilling()}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
          >
            새로고침
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
