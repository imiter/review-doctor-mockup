"use client";

import { useEffect, useState } from "react";
import { loadTossPayments, type TossPaymentsWidgets } from "@tosspayments/tosspayments-sdk";
import { Card } from "@/components/Card";
import { apiGet, apiPost, ApiError, won } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type PaymentHistoryItem = {
  order_id: string;
  amount: number;
  status: "pending" | "approved" | "failed";
  requested_at: string;
  approved_at: string | null;
};

type CheckoutInfo = { order_id: string; amount: number; order_name: string };

const STATUS_LABEL: Record<PaymentHistoryItem["status"], string> = {
  pending: "대기중",
  approved: "승인완료",
  failed: "실패",
};

const PRO_PRICE = 19900;

export default function BillingPage() {
  const { user, billing } = useStoreContext();
  const [history, setHistory] = useState<PaymentHistoryItem[]>([]);
  const [checkoutOpen, setCheckoutOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkout, setCheckout] = useState<CheckoutInfo | null>(null);
  const [widgets, setWidgets] = useState<TossPaymentsWidgets | null>(null);
  const [widgetsReady, setWidgetsReady] = useState(false);

  useEffect(() => {
    apiGet<PaymentHistoryItem[]>("/billing/history").then(setHistory).catch(() => {});
  }, []);

  // 결제위젯 초기화("Pro 시작하기" 클릭 시 1회). 순서: 주문 생성 -> SDK 로드 -> 위젯 생성
  // -> setAmount -> renderPaymentMethods/renderAgreement. setAmount는 반드시 render* 이전에
  // 호출해야 한다(토스 SDK 제약).
  const clientKey = process.env.NEXT_PUBLIC_TOSS_CLIENT_KEY;

  useEffect(() => {
    if (!checkoutOpen || !clientKey) return;

    let cancelled = false;

    (async () => {
      setError(null);
      setWidgetsReady(false);
      try {
        const checkoutInfo = await apiPost<CheckoutInfo>("/billing/checkout", {});
        if (cancelled) return;
        setCheckout(checkoutInfo);

        const tossPayments = await loadTossPayments(clientKey);
        if (cancelled) return;

        // customerKey는 구매자를 식별하는 고유 아이디. 토스 SDK 문서에 따르면 이메일·회원
        // DB id처럼 유추 가능한 값은 안전하지 않아 사용이 금지되어 있어, 결제 시도마다
        // 무작위 값을 새로 생성한다(이 앱은 브랜드페이/저장 결제수단을 쓰지 않아 매번 새로
        // 발급해도 무방하다).
        const paymentWidgets = tossPayments.widgets({ customerKey: crypto.randomUUID() });
        await paymentWidgets.setAmount({ currency: "KRW", value: checkoutInfo.amount });
        if (cancelled) return;

        await Promise.all([
          paymentWidgets.renderPaymentMethods({ selector: "#toss-payment-method", variantKey: "DEFAULT" }),
          paymentWidgets.renderAgreement({ selector: "#toss-agreement", variantKey: "AGREEMENT" }),
        ]);
        if (cancelled) return;

        setWidgets(paymentWidgets);
        setWidgetsReady(true);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : "결제 위젯을 불러오지 못했습니다.");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [checkoutOpen, clientKey]);

  const handlePay = async () => {
    if (!widgets || !checkout) return;
    try {
      // 성공/실패 시 successUrl/failUrl로 리다이렉트되며, 그 화면에서 /billing/confirm을
      // 호출해 결제를 최종 승인한다(Task 8).
      await widgets.requestPayment({
        orderId: checkout.order_id,
        orderName: checkout.order_name,
        successUrl: `${window.location.origin}/account/billing/success`,
        failUrl: `${window.location.origin}/account/billing/fail`,
        customerEmail: user?.email ?? undefined,
        customerName: user?.nickname ?? undefined,
      });
    } catch (e) {
      // 사용자가 결제창을 닫는 등의 취소는 여기로도 들어올 수 있어 에러 배너만 띄운다.
      setError(e instanceof Error ? e.message : "결제 요청 중 오류가 발생했습니다.");
    }
  };

  const isPro = billing?.is_pro ?? false;

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">구독 관리</h1>
        <p className="text-sm text-muted">플랜과 결제 내역을 확인하고 Pro로 업그레이드할 수 있어요.</p>
      </div>

      <Card title="현재 구독">
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted">현재 요금제</span>
          <span
            className={`rounded px-2 py-0.5 text-xs font-medium ${isPro ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted"}`}
          >
            {isPro ? "Pro" : "Basic"}
          </span>
        </div>
        {isPro && billing?.expires_at && (
          <p className="mt-2 text-sm text-muted">다음 결제 예정일: {billing.expires_at}</p>
        )}

        <div className="mt-4">
          <p className="mb-2 text-sm text-muted">결제 내역</p>
          {history.length === 0 ? (
            <p className="text-sm text-muted">결제 내역이 없습니다.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs text-muted">
                  <th className="py-2 text-left">일시</th>
                  <th className="py-2 text-left">금액</th>
                  <th className="py-2 text-left">상태</th>
                </tr>
              </thead>
              <tbody>
                {history.map((p) => (
                  <tr key={p.order_id} className="border-b border-border-subtle last:border-0">
                    <td className="py-2">{new Date(p.requested_at).toLocaleString("ko-KR")}</td>
                    <td className="py-2">{won(p.amount)}</td>
                    <td className="py-2">{STATUS_LABEL[p.status]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card title="Basic">
          <p className="text-2xl font-semibold">무료</p>
          <ul className="mt-3 space-y-1.5 text-sm text-muted">
            <li>답글 생성 하루 10건</li>
            <li>광고 순위 모니터링 🔒</li>
            <li>리뷰 관리·매출·정산·주문내역·재주문율 통계</li>
          </ul>
          {!isPro && (
            <div className="mt-4 rounded-lg border border-border-subtle py-2.5 text-center text-sm text-muted">
              현재 플랜
            </div>
          )}
        </Card>

        <Card title="Pro">
          <p className="text-2xl font-semibold">
            {won(PRO_PRICE)}
            <span className="text-sm font-normal text-muted"> /월</span>
          </p>
          <ul className="mt-3 space-y-1.5 text-sm text-muted">
            <li>답글 생성 무제한</li>
            <li>광고 순위 모니터링 전체 이용</li>
            <li>Basic의 모든 기능 포함</li>
          </ul>
          {isPro ? (
            <div className="mt-4 rounded-lg bg-accent-soft py-2.5 text-center text-sm text-accent">현재 플랜</div>
          ) : (
            <button
              onClick={() => setCheckoutOpen(true)}
              disabled={checkoutOpen}
              className="mt-4 w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              Pro 시작하기
            </button>
          )}
        </Card>
      </div>

      {checkoutOpen && !isPro && (
        <Card title="결제하기">
          {!clientKey && (
            <p className="mb-3 text-sm text-danger">
              토스페이먼츠 클라이언트키가 설정되지 않았습니다. NEXT_PUBLIC_TOSS_CLIENT_KEY를 확인해주세요.
            </p>
          )}
          {error && <p className="mb-3 text-sm text-danger">{error}</p>}
          <div id="toss-payment-method" />
          <div id="toss-agreement" className="mt-3" />
          <button
            onClick={handlePay}
            disabled={!widgetsReady}
            className="mt-4 w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {won(PRO_PRICE)} 결제하기
          </button>
        </Card>
      )}
    </div>
  );
}
