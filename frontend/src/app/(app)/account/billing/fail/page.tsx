"use client";

import { useRouter, useSearchParams } from "next/navigation";

export default function BillingFailPage() {
  const params = useSearchParams();
  const router = useRouter();
  const message = params.get("message") ?? "결제가 취소되었거나 실패했습니다.";

  return (
    <div className="mx-auto max-w-md space-y-4 py-16 text-center">
      <p className="text-sm text-danger">{message}</p>
      <button
        onClick={() => router.push("/account/billing")}
        className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
      >
        다시 시도하기
      </button>
    </div>
  );
}
