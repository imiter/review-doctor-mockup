"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, apiPost } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type ReplyStyle = { id: number; name: string; description: string };
type ReplyRef = { content: string; style_id: number } | null;
type Review = {
  id: number;
  platform_name: string;
  menu_summary: string;
  rating: number;
  content: string;
  customer_nickname: string;
  customer_order_count: number;
  status: "unanswered" | "pending" | "answered";
  created_at: string;
  final_reply: ReplyRef;
  draft_reply: ReplyRef;
};

const FILTERS = [
  { key: "unanswered", label: "답글 대기" },
  { key: "pending", label: "검토 중" },
  { key: "answered", label: "답글 완료" },
  { key: "", label: "전체" },
] as const;

function ReviewCard({ review, styles, onSaved }: { review: Review; styles: ReplyStyle[]; onSaved: () => void }) {
  const [styleId, setStyleId] = useState(review.draft_reply?.style_id ?? styles[0]?.id ?? 0);
  const [draft, setDraft] = useState(review.draft_reply?.content ?? "");
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (styles.length > 0 && styleId === 0) setStyleId(styles[0].id);
  }, [styles, styleId]);

  const generate = async () => {
    setGenerating(true);
    try {
      const res = await apiPost<{ content: string }>(`/reviews/${review.id}/generate-reply`, { style_id: styleId });
      setDraft(res.content);
      onSaved();
    } finally {
      setGenerating(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPost(`/reviews/${review.id}/reply`, { style_id: styleId, content: draft });
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl border border-border-subtle bg-surface-2 p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="rounded bg-surface px-2 py-0.5 font-medium text-accent">{review.platform_name}</span>
        <span className="text-warning">{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span>
        <span className="font-medium text-foreground">{review.customer_nickname}</span>
        <span>· {review.customer_order_count}회 주문</span>
        <span className="ml-auto">{new Date(review.created_at).toLocaleString("ko-KR")}</span>
      </div>
      <p className="mt-2 text-xs text-muted">{review.menu_summary}</p>
      <p className="mt-1 text-sm text-foreground">{review.content}</p>

      {review.final_reply ? (
        <div className="mt-3 rounded-lg border border-border-subtle bg-surface p-3">
          <p className="mb-1 text-xs font-medium text-success">등록된 답글</p>
          <p className="text-sm text-foreground">{review.final_reply.content}</p>
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={styleId}
              onChange={(e) => setStyleId(Number(e.target.value))}
              className="rounded-lg border border-border-subtle bg-surface px-2 py-1.5 text-xs outline-none focus:border-accent"
            >
              {styles.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
            <button
              onClick={generate}
              disabled={generating}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {generating ? "생성 중..." : "답글 생성 (Mock)"}
            </button>
          </div>
          {draft && (
            <div className="space-y-2">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-border-subtle bg-surface p-2.5 text-sm outline-none focus:border-accent"
              />
              <button
                onClick={save}
                disabled={saving}
                className="rounded-lg border border-success px-3 py-1.5 text-xs font-medium text-success transition hover:bg-success/10 disabled:opacity-50"
              >
                {saving ? "등록 중..." : "답글 등록하기"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ReviewsPage() {
  const { storeId } = useStoreContext();
  const [reviews, setReviews] = useState<Review[]>([]);
  const [styles, setStyles] = useState<ReplyStyle[]>([]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("unanswered");

  const load = useCallback(async () => {
    if (!storeId) return;
    const qs = filter ? `&status=${filter}` : "";
    setReviews(await apiGet<Review[]>(`/reviews?store_id=${storeId}${qs}`));
  }, [storeId, filter]);

  useEffect(() => {
    apiGet<ReplyStyle[]>("/reply-styles").then(setStyles);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">리뷰 관리</h1>
        <p className="text-sm text-muted">답글 생성은 스타일 템플릿 기반 Mock — 실제 AI 호출 없음</p>
      </div>

      <div className="flex gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              filter === f.key ? "bg-accent text-white" : "border border-border-subtle text-muted hover:text-foreground"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <Card>
        <div className="space-y-3">
          {reviews.length === 0 ? (
            <p className="text-sm text-muted">해당하는 리뷰가 없습니다.</p>
          ) : (
            reviews.map((r) => <ReviewCard key={r.id} review={r} styles={styles} onSaved={load} />)
          )}
        </div>
      </Card>
    </div>
  );
}
