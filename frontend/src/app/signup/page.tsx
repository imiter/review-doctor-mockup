"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, apiPost, setToken } from "@/lib/api";

type TokenResponse = { access_token: string };

export default function SignupPage() {
  const router = useRouter();
  const [form, setForm] = useState({ email: "", password: "", nickname: "", phone: "", marketing_agreed: false });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const update = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await apiPost<TokenResponse>("/auth/signup", {
        email: form.email,
        password: form.password,
        nickname: form.nickname,
        phone: form.phone || undefined,
        marketing_agreed: form.marketing_agreed,
      });
      setToken(res.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "회원가입에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-6">
          <p className="text-base font-semibold">회원가입</p>
          <p className="text-xs text-muted">이메일 로그인만 지원합니다 (소셜 로그인은 추후 지원)</p>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-muted">닉네임</label>
            <input
              required value={form.nickname} onChange={update("nickname")}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="김사장"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">이메일</label>
            <input
              type="email" required value={form.email} onChange={update("email")}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="you@store.com"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">비밀번호</label>
            <input
              type="password" required minLength={8} value={form.password} onChange={update("password")}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="8자 이상"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">휴대폰 번호 (선택, 해시로만 저장)</label>
            <input
              value={form.phone} onChange={update("phone")}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              placeholder="010-0000-0000"
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={form.marketing_agreed} onChange={update("marketing_agreed")} className="accent-accent" />
            마케팅 정보 수신에 동의합니다 (선택)
          </label>

          {error && <p className="text-xs text-danger">{error}</p>}

          <button
            type="submit" disabled={loading}
            className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {loading ? "가입 중..." : "가입하고 시작하기"}
          </button>
        </form>

        <p className="mt-6 text-center text-xs text-muted">
          이미 계정이 있나요?{" "}
          <Link href="/login" className="text-accent hover:underline">
            로그인
          </Link>
        </p>
      </div>
    </main>
  );
}
