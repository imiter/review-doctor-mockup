"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiPatch } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

export default function ProfilePage() {
  const { user, refreshUser } = useStoreContext();
  const [nickname, setNickname] = useState("");
  const [phone, setPhone] = useState("");
  const [marketingAgreed, setMarketingAgreed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (user) {
      setNickname(user.nickname);
      setMarketingAgreed(user.marketing_agreed);
    }
  }, [user]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    try {
      await apiPatch("/auth/me", {
        nickname,
        phone: phone.trim() || undefined,
        marketing_agreed: marketingAgreed,
      });
      await refreshUser();
      setPhone("");
      setSaved(true);
    } finally {
      setSaving(false);
    }
  };

  if (!user) return <p className="text-sm text-muted">불러오는 중...</p>;

  return (
    <div className="max-w-lg space-y-6">
      <div>
        <h1 className="text-xl font-semibold">계정 관리</h1>
      </div>

      <Card>
        <form onSubmit={save} className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-muted">이름</label>
            <input
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">이메일</label>
            <input
              value={user.email}
              disabled
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm text-muted"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">
              휴대번호 {user.has_phone && <span className="text-success">· 등록됨</span>}
            </label>
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="010-0000-0000"
              className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
            />
            <p className="mt-1 text-[11px] text-muted">원문은 저장하지 않고 해시로만 저장됩니다.</p>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={marketingAgreed}
              onChange={(e) => setMarketingAgreed(e.target.checked)}
              className="accent-accent"
            />
            (선택) 마케팅 수신 동의
          </label>

          <button
            type="submit"
            disabled={saving}
            className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "저장 중..." : "저장"}
          </button>
          {saved && <p className="text-center text-xs text-success">저장되었습니다.</p>}
        </form>
      </Card>
    </div>
  );
}
