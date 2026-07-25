"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, apiPut } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type ReplyStyle = { id: number; name: string; description: string };
type ReplySettings = {
  style_id: number;
  promo_text: string | null;
  include_nickname: boolean;
  include_menu: boolean;
  include_store_name: boolean;
  promo_on_negative: boolean;
};

export default function ReplyStylesPage() {
  const { storeId } = useStoreContext();
  const [styles, setStyles] = useState<ReplyStyle[]>([]);
  const [settings, setSettings] = useState<ReplySettings | null>(null);
  const [promoDraft, setPromoDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiGet<ReplyStyle[]>("/reply-styles").then(setStyles);
  }, []);
  useEffect(() => {
    if (!storeId) return;
    apiGet<ReplySettings>(`/reply-settings?store_id=${storeId}`).then((s) => {
      setSettings(s);
      setPromoDraft(s.promo_text ?? "");
    });
  }, [storeId]);

  const save = async (patch: Partial<ReplySettings>) => {
    if (!settings || !storeId) return;
    const next = { ...settings, ...patch };
    setSettings(next);
    setSaving(true);
    try {
      await apiPut(`/reply-settings?store_id=${storeId}`, patch);
    } finally {
      setSaving(false);
    }
  };

  if (!settings) return <p className="text-sm text-muted">불러오는 중...</p>;

  const checkboxes: { key: keyof ReplySettings; label: string }[] = [
    { key: "include_nickname", label: "답글에 고객 닉네임 포함" },
    { key: "include_menu", label: "답글에 메뉴 정보 포함" },
    { key: "include_store_name", label: "답글에 가게 이름 포함" },
    { key: "promo_on_negative", label: "부정 리뷰에도 홍보 문구 등록" },
  ];

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">답글 스타일 설정</h1>
        <p className="text-sm text-muted">원하는 답글 스타일을 선택하세요. 각 스타일은 답변 톤과 방식이 다릅니다.</p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {styles.map((s) => {
          const active = settings.style_id === s.id;
          return (
            <button
              key={s.id}
              onClick={() => save({ style_id: s.id })}
              className={`rounded-xl border p-4 text-left transition ${
                active ? "border-accent bg-accent-soft" : "border-border-subtle bg-surface hover:bg-surface-2"
              }`}
            >
              <div className="flex items-center justify-between">
                <p className={`text-sm font-semibold ${active ? "text-accent" : "text-foreground"}`}>{s.name}</p>
                {active && <span className="text-accent">✓</span>}
              </div>
              <p className="mt-1 text-xs text-muted">{s.description}</p>
            </button>
          );
        })}
      </div>

      <Card title="홍보 문구 설정">
        <p className="mb-3 text-xs text-muted">답글 마지막에 자동으로 추가될 홍보 문구나 매장 정보를 입력하세요.</p>
        <textarea
          value={promoDraft}
          onChange={(e) => setPromoDraft(e.target.value)}
          onBlur={() => save({ promo_text: promoDraft })}
          rows={3}
          maxLength={400}
          placeholder="예) 매주 화요일은 서비스 데이! 리뷰 이벤트도 진행 중이에요"
          className="w-full rounded-lg border border-border-subtle bg-surface-2 p-3 text-sm outline-none focus:border-accent"
        />
        <p className="mt-1 text-right text-[11px] text-muted">{promoDraft.length}/400자</p>
      </Card>

      <Card title="답글 상세 설정">
        <div className="space-y-3">
          {checkboxes.map((c) => (
            <label key={c.key} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={Boolean(settings[c.key])}
                onChange={(e) => save({ [c.key]: e.target.checked } as Partial<ReplySettings>)}
                className="accent-accent"
              />
              {c.label}
            </label>
          ))}
        </div>
        <p className="mt-4 text-xs text-muted">{saving ? "저장 중..." : ""}</p>
      </Card>
    </div>
  );
}
