"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, apiPut } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type ReplySettings = {
  style_id: number;
  auto_reply_enabled: boolean;
  auto_reply_min_rating: number;
};
type ReplyStyle = { id: number; name: string; description: string };

export default function ReplyRulesPage() {
  const { storeId } = useStoreContext();
  const [settings, setSettings] = useState<ReplySettings | null>(null);
  const [styles, setStyles] = useState<ReplyStyle[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!storeId) return;
    apiGet<ReplySettings>(`/reply-settings?store_id=${storeId}`).then(setSettings);
  }, [storeId]);
  useEffect(() => {
    apiGet<ReplyStyle[]>("/reply-styles").then(setStyles);
  }, []);

  const save = async (patch: Partial<ReplySettings>) => {
    if (!settings || !storeId) return;
    const next = { ...settings, ...patch };
    setSettings(next);
    setSaving(true);
    setSaved(false);
    try {
      await apiPut(`/reply-settings?store_id=${storeId}`, patch);
      setSaved(true);
    } finally {
      setSaving(false);
    }
  };

  if (!settings) return <p className="text-sm text-muted">불러오는 중...</p>;

  const currentStyle = styles.find((s) => s.id === settings.style_id);

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">답글 규칙 설정</h1>
        <p className="text-sm text-muted">
          자동 답글을 켜면 새로 들어오는 5점 리뷰에 한해 사람 확인 없이 실제 배민에 답글이
          자동으로 등록됩니다. 아직 안전을 위해 5점 리뷰만 지원해요 — 4점 이하는 이 설정과
          무관하게 자동 등록되지 않습니다.
        </p>
      </div>

      <Card>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">자동 답글</p>
            <p className="text-xs text-muted">{settings.auto_reply_enabled ? "켜짐" : "꺼짐"}</p>
          </div>
          <button
            onClick={() => save({ auto_reply_enabled: !settings.auto_reply_enabled })}
            className={`relative h-7 w-12 rounded-full transition ${settings.auto_reply_enabled ? "bg-accent" : "bg-surface-2"}`}
          >
            <span
              className={`absolute top-1 h-5 w-5 rounded-full bg-white transition ${settings.auto_reply_enabled ? "left-6" : "left-1"}`}
            />
          </button>
        </div>

        {settings.auto_reply_enabled && (
          <div className="mt-5 space-y-5 border-t border-border-subtle pt-5">
            <div>
              <label className="mb-2 block text-xs text-muted">
                몇 점 이상 리뷰에 자동 답글을 적용할까요? (지금은 5점만 지원)
              </label>
              <div className="flex gap-2">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button
                    key={n}
                    disabled={n !== 5}
                    title={n !== 5 ? "아직 5점 리뷰만 지원해요" : undefined}
                    onClick={() => save({ auto_reply_min_rating: n })}
                    className={`flex-1 rounded-lg py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-30 ${
                      settings.auto_reply_min_rating === n
                        ? "bg-accent text-white"
                        : "border border-border-subtle text-muted hover:text-foreground"
                    }`}
                  >
                    {n}점 ↑
                  </button>
                ))}
              </div>
              <p className="mt-2 text-xs text-muted">
                현재: 5점 리뷰만 자동 답글 대상 (4점 이하는 안전을 위해 아직 미지원)
              </p>
            </div>

            <div className="rounded-lg bg-surface-2 p-3">
              <p className="mb-1 text-xs text-muted">적용될 답글 스타일</p>
              {currentStyle ? (
                <>
                  <p className="text-sm font-semibold text-accent">{currentStyle.name}</p>
                  <p className="mt-0.5 text-xs text-muted">{currentStyle.description}</p>
                </>
              ) : (
                <p className="text-sm text-muted">불러오는 중...</p>
              )}
              <Link href="/reviews/styles" className="mt-2 inline-block text-xs text-accent hover:underline">
                답글 스타일 설정에서 변경 →
              </Link>
            </div>
          </div>
        )}

        <p className="mt-4 text-xs text-muted">{saving ? "저장 중..." : saved ? "저장됨" : ""}</p>
      </Card>
    </div>
  );
}
