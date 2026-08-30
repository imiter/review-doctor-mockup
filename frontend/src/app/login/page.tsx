"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ApiError, apiPost, getToken, setToken } from "@/lib/api";
import { kakaoAuthorizeUrl } from "@/lib/kakao";
import { Logo } from "@/components/Logo";

type TokenResponse = { access_token: string };

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (typeof window !== "undefined" && getToken()) {
    router.replace("/dashboard");
  }

  const resetSuccess = searchParams.get("reset") === "success";

  const submit = async (e: React.FormEvent, overrideEmail?: string, overridePassword?: string) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await apiPost<TokenResponse>("/auth/login", {
        email: overrideEmail ?? email,
        password: overridePassword ?? password,
      });
      setToken(res.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "로그인에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-8 flex items-center gap-2.5">
          <Logo size={36} />
          <div>
            <p className="text-base font-semibold">스토어 타겟</p>
            <p className="text-xs text-muted">Store Target</p>
          </div>
        </div>

        {resetSuccess && (
          <p className="mb-4 rounded-lg border border-accent/40 bg-accent-soft px-3 py-2 text-xs text-accent">
            비밀번호가 재설정되었습니다. 새 비밀번호로 로그인해주세요.
          </p>
        )}

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-muted">이메일</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="you@store.com"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">비밀번호</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="••••••••"
            />
          </div>

          {error && <p className="text-xs text-danger">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {loading ? "로그인 중..." : "로그인"}
          </button>
        </form>

        <p className="mt-3 text-center text-xs">
          <Link href="/password-reset" className="text-muted hover:text-foreground hover:underline">
            비밀번호를 잊으셨나요?
          </Link>
        </p>

        <button
          onClick={(e) => submit(e, "demo@dris.kr", "Dntm156!@")}
          disabled={loading}
          className="mt-3 w-full rounded-lg border border-border-subtle py-2.5 text-sm text-muted transition hover:border-accent hover:text-foreground disabled:opacity-50"
        >
          데모 계정으로 로그인
        </button>

        <button
          type="button"
          onClick={() => {
            try {
              window.location.href = kakaoAuthorizeUrl();
            } catch (err) {
              setError(err instanceof Error ? err.message : "카카오 로그인을 시작할 수 없습니다");
            }
          }}
          className="mt-3 w-full rounded-lg bg-[#FEE500] py-2.5 text-sm font-medium text-black transition hover:opacity-90"
        >
          카카오로 로그인
        </button>

        <p className="mt-6 text-center text-xs text-muted">
          계정이 없나요?{" "}
          <Link href="/signup" className="text-accent hover:underline">
            회원가입
          </Link>
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
