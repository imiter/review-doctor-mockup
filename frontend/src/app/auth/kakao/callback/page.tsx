"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError, apiPost, setToken } from "@/lib/api";
import { kakaoRedirectUri } from "@/lib/kakao";

type TokenResponse = { access_token: string };

function KakaoCallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const submitted = useRef(false);

  useEffect(() => {
    if (submitted.current) return;
    submitted.current = true;

    const code = searchParams.get("code");
    if (!code) {
      setError("카카오 로그인 코드가 없습니다");
      return;
    }

    apiPost<TokenResponse>("/auth/kakao/callback", { code, redirect_uri: kakaoRedirectUri() })
      .then((res) => {
        setToken(res.access_token);
        router.push("/dashboard");
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "카카오 로그인에 실패했습니다");
      });
  }, [searchParams, router]);

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 text-center">
        {error ? (
          <>
            <p className="mb-4 text-xs text-danger">{error}</p>
            <a href="/login" className="text-sm text-accent hover:underline">
              로그인으로 돌아가기
            </a>
          </>
        ) : (
          <p className="text-sm text-muted">카카오 로그인 처리 중...</p>
        )}
      </div>
    </main>
  );
}

export default function KakaoCallbackPage() {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center">
          <p className="text-sm text-muted">로딩 중...</p>
        </main>
      }
    >
      <KakaoCallbackInner />
    </Suspense>
  );
}
