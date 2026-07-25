"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, apiPost } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type SecondaryReply = { id: number; content: string; created_at: string };
type Review = {
  id: number;
  platform_name: string;
  menu_summary: string;
  rating: number;
  content: string;
  customer_nickname: string;
  final_reply: { content: string } | null;
  secondary_replies: SecondaryReply[];
};

function SecondaryReplyCard({ review, onSaved }: { review: Review; onSaved: () => void }) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!text.trim()) return;
    setSaving(true);
    try {
      await apiPost(`/reviews/${review.id}/secondary-reply`, { content: text });
      setText("");
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl border border-border-subtle bg-surface-2 p-4">
      <div className="flex items-center gap-2 text-xs text-muted">
        <span className="rounded bg-surface px-2 py-0.5 font-medium text-accent">{review.platform_name}</span>
        <span className="text-warning">{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span>
        <span className="font-medium text-foreground">{review.customer_nickname}</span>
      </div>
      <p className="mt-2 text-sm text-foreground">{review.content}</p>

      <div className="mt-3 rounded-lg border border-border-subtle bg-surface p-3">
        <p className="mb-1 text-xs font-medium text-success">1차 답글</p>
        <p className="text-sm">{review.final_reply?.content}</p>
      </div>

      {review.secondary_replies.map((r) => (
        <div key={r.id} className="mt-2 rounded-lg border border-accent/30 bg-accent-soft p-3">
          <p className="mb-1 text-xs font-medium text-accent">2차 답글</p>
          <p className="text-sm">{r.content}</p>
        </div>
      ))}

      <div className="mt-3 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="추가로 안내할 내용을 입력하세요"
          className="flex-1 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <button
          onClick={save}
          disabled={saving || !text.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
        >
          {saving ? "등록 중..." : "2차 답글 등록"}
        </button>
      </div>
    </div>
  );
}

export default function SecondaryReplyPage() {
  const { storeId } = useStoreContext();
  const [reviews, setReviews] = useState<Review[]>([]);

  const load = useCallback(async () => {
    if (!storeId) return;
    const all = await apiGet<Review[]>(`/reviews?store_id=${storeId}&status=answered`);
    setReviews(all);
  }, [storeId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">2차 답글 등록</h1>
        <p className="text-sm text-muted">
          이미 1차 답글을 등록한 리뷰에 추가 안내를 덧붙입니다 — 고객이 리뷰를 수정했거나
          추가 안내가 필요할 때 사용합니다.
        </p>
      </div>

      <Card>
        <div className="space-y-3">
          {reviews.length === 0 ? (
            <p className="text-sm text-muted">답글 완료된 리뷰가 없습니다.</p>
          ) : (
            reviews.map((r) => <SecondaryReplyCard key={r.id} review={r} onSaved={load} />)
          )}
        </div>
      </Card>
    </div>
  );
}
