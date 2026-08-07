"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, apiPost } from "@/lib/api";

type Step = "email" | "reset";

export default function PasswordResetPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const sendCode = async () => {
    setError(null);
    setLoading(true);
    try {
      await apiPost("/auth/password-reset/request", { email });
      setCode("");
      setStep("reset");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호 발송에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword !== newPasswordConfirm) {
      setError("비밀번호가 일치하지 않습니다");
      return;
    }
    setLoading(true);
    try {
      await apiPost("/auth/password-reset/confirm", { email, code, new_password: newPassword });
      router.push("/login?reset=success");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "비밀번호 재설정에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-6">
          <p className="text-base font-semibold">비밀번호 찾기</p>
          <p className="text-xs text-muted">가입한 이메일로 인증번호를 보내드립니다</p>
        </div>

        {error && <p className="mb-4 text-xs text-danger">{error}</p>}

        {step === "email" && (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-muted">이메일</label>
              <input
                type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="you@store.com"
              />
            </div>
            <button
              type="button" disabled={loading || !email}
              onClick={sendCode}
              className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {loading ? "발송 중..." : "인증번호 받기"}
            </button>
          </div>
        )}

        {step === "reset" && (
          <form onSubmit={submit} className="space-y-4">
            <p className="text-xs text-muted">{email}(으)로 인증번호를 보냈습니다.</p>
            <div>
              <label className="mb-1 block text-xs text-muted">인증번호</label>
              <input
                required autoFocus inputMode="numeric" maxLength={6}
                value={code} onChange={(e) => setCode(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="6자리 숫자"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">새 비밀번호</label>
              <input
                type="password" required minLength={8}
                value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="8자 이상"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">새 비밀번호 확인</label>
              <input
                type="password" required minLength={8}
                value={newPasswordConfirm} onChange={(e) => setNewPasswordConfirm(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="비밀번호를 한 번 더 입력해주세요"
              />
            </div>
            <div className="flex gap-2">
              <button
                type="button" onClick={() => setStep("email")}
                className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2"
              >
                뒤로
              </button>
              <button
                type="submit" disabled={loading}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "변경 중..." : "비밀번호 재설정"}
              </button>
            </div>
          </form>
        )}

        <p className="mt-6 text-center text-xs text-muted">
          <Link href="/login" className="text-accent hover:underline">
            로그인으로 돌아가기
          </Link>
        </p>
      </div>
    </main>
  );
}
