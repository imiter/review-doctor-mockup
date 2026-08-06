"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, apiPost, setToken } from "@/lib/api";

type Step = "email" | "email-code" | "phone" | "phone-code" | "password";
const STEPS: Step[] = ["email", "email-code", "phone", "phone-code", "password"];

type TokenResponse = { access_token: string };

export default function SignupPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("email");
  const [nickname, setNickname] = useState("");
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneCode, setPhoneCode] = useState("");
  const [mockPhoneCode, setMockPhoneCode] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [marketingAgreed, setMarketingAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setInterval(() => setCooldown((c) => Math.max(0, c - 1)), 1000);
    return () => clearInterval(timer);
  }, [cooldown]);

  const goBack = () => {
    setError(null);
    const idx = STEPS.indexOf(step);
    if (idx > 0) setStep(STEPS[idx - 1]);
  };

  const sendEmailCode = async () => {
    setError(null);
    setLoading(true);
    try {
      await apiPost("/auth/signup/email-code", { email });
      setEmailCode("");
      setStep("email-code");
      setCooldown(60);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호 발송에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  const confirmEmailCode = async () => {
    setError(null);
    setLoading(true);
    try {
      await apiPost("/auth/signup/verify-email-code", { email, code: emailCode });
      setStep("phone");
      setCooldown(0);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호가 올바르지 않습니다");
    } finally {
      setLoading(false);
    }
  };

  const sendPhoneCode = async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await apiPost<{ mock_code: string }>("/auth/signup/phone-code", { phone });
      setMockPhoneCode(res.mock_code);
      setPhoneCode("");
      setStep("phone-code");
      setCooldown(60);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호 발급에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  const confirmPhoneCode = async () => {
    setError(null);
    setLoading(true);
    try {
      await apiPost("/auth/signup/verify-phone-code", { phone, code: phoneCode });
      setStep("password");
      setCooldown(0);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "인증번호가 올바르지 않습니다");
    } finally {
      setLoading(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password !== passwordConfirm) {
      setError("비밀번호가 일치하지 않습니다");
      return;
    }
    setLoading(true);
    try {
      const res = await apiPost<TokenResponse>("/auth/signup", {
        email,
        email_code: emailCode,
        phone,
        phone_code: phoneCode,
        password,
        nickname,
        marketing_agreed: marketingAgreed,
      });
      setToken(res.access_token);
      router.push("/dashboard");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "회원가입에 실패했습니다");
    } finally {
      setLoading(false);
    }
  };

  const stepIndex = STEPS.indexOf(step);

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-8 shadow-2xl shadow-black/40">
        <div className="mb-6">
          <p className="text-base font-semibold">회원가입</p>
          <p className="text-xs text-muted">이메일 로그인만 지원합니다 (소셜 로그인은 별도)</p>
          <div className="mt-3 flex gap-1.5">
            {STEPS.map((s, i) => (
              <div key={s} className={`h-1 flex-1 rounded-full ${i <= stepIndex ? "bg-accent" : "bg-surface-2"}`} />
            ))}
          </div>
        </div>

        {error && <p className="mb-4 text-xs text-danger">{error}</p>}

        {step === "email" && (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-muted">닉네임</label>
              <input
                required autoFocus value={nickname} onChange={(e) => setNickname(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="김사장"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">이메일</label>
              <input
                type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="you@store.com"
              />
            </div>
            <button
              type="button" disabled={loading || !nickname || !email}
              onClick={sendEmailCode}
              className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {loading ? "발송 중..." : "인증번호 받기"}
            </button>
          </div>
        )}

        {step === "email-code" && (
          <div className="space-y-4">
            <p className="text-xs text-muted">{email}(으)로 인증번호를 보냈습니다.</p>
            <div>
              <label className="mb-1 block text-xs text-muted">이메일 인증번호</label>
              <input
                required autoFocus inputMode="numeric" maxLength={6}
                value={emailCode} onChange={(e) => setEmailCode(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="6자리 숫자"
              />
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={goBack} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
                뒤로
              </button>
              <button
                type="button" disabled={loading || emailCode.length !== 6}
                onClick={confirmEmailCode}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "확인 중..." : "확인"}
              </button>
            </div>
            <button
              type="button" disabled={cooldown > 0 || loading} onClick={sendEmailCode}
              className="w-full text-center text-xs text-accent hover:underline disabled:text-muted disabled:no-underline"
            >
              {cooldown > 0 ? `재전송 (${cooldown}초 후 가능)` : "인증번호 재전송"}
            </button>
          </div>
        )}

        {step === "phone" && (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-muted">휴대폰 번호</label>
              <input
                required autoFocus value={phone} onChange={(e) => setPhone(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="010-0000-0000"
              />
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={goBack} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
                뒤로
              </button>
              <button
                type="button" disabled={loading || !phone}
                onClick={sendPhoneCode}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "발급 중..." : "인증번호 받기"}
              </button>
            </div>
          </div>
        )}

        {step === "phone-code" && (
          <div className="space-y-4">
            <p className="rounded-lg border border-accent/40 bg-accent-soft px-3 py-2 text-xs text-accent">
              데모용 인증번호: <b>{mockPhoneCode}</b> (Mock — 실제 문자는 발송되지 않습니다)
            </p>
            <div>
              <label className="mb-1 block text-xs text-muted">휴대폰 인증번호</label>
              <input
                required autoFocus inputMode="numeric" maxLength={6}
                value={phoneCode} onChange={(e) => setPhoneCode(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="6자리 숫자"
              />
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={goBack} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
                뒤로
              </button>
              <button
                type="button" disabled={loading || phoneCode.length !== 6}
                onClick={confirmPhoneCode}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "확인 중..." : "확인"}
              </button>
            </div>
            <button
              type="button" disabled={cooldown > 0 || loading} onClick={sendPhoneCode}
              className="w-full text-center text-xs text-accent hover:underline disabled:text-muted disabled:no-underline"
            >
              {cooldown > 0 ? `재전송 (${cooldown}초 후 가능)` : "인증번호 재전송"}
            </button>
          </div>
        )}

        {step === "password" && (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-muted">비밀번호</label>
              <input
                type="password" required minLength={8} autoFocus
                value={password} onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="8자 이상"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">비밀번호 확인</label>
              <input
                type="password" required minLength={8}
                value={passwordConfirm} onChange={(e) => setPasswordConfirm(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="비밀번호를 한 번 더 입력해주세요"
              />
            </div>
            <label className="flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox" checked={marketingAgreed}
                onChange={(e) => setMarketingAgreed(e.target.checked)}
                className="accent-accent"
              />
              마케팅 정보 수신에 동의합니다 (선택)
            </label>
            <div className="flex gap-2">
              <button type="button" onClick={goBack} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
                뒤로
              </button>
              <button
                type="submit" disabled={loading}
                className="flex-1 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "가입 중..." : "가입 완료"}
              </button>
            </div>
          </form>
        )}

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
