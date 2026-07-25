"use client";
import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";

type Reply = { content: string; style_id: number };
type Review = {
  id: number; store_name: string; platform_name: string; rating: number;
  content: string; reviewer_name: string; status: string; created_at: string;
  reply: Reply | null;
};
type Style = { id: number; name: string; description: string };

function ReviewCard({ review, styles, onSaved }: { review: Review; styles: Style[]; onSaved: () => void }) {
  const [styleId, setStyleId] = useState(styles[0]?.id ?? 0);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    if (styles.length > 0 && styleId === 0) setStyleId(styles[0].id);
  }, [styles, styleId]);

  const generate = async () => {
    const res = await apiPost<{ content: string }>(`/api/reviews/${review.id}/reply/draft`, { style_id: styleId });
    setDraft(res.content);
  };
  const save = async () => {
    await apiPost(`/api/reviews/${review.id}/reply`, { style_id: styleId, content: draft });
    onSaved();
  };

  return (
    <div className="rounded-lg border p-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-amber-500">{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span>
        <span className="font-medium">{review.reviewer_name}</span>
        <span className="text-gray-400">{review.store_name} · {review.platform_name}</span>
      </div>
      <p className="mt-2">{review.content}</p>
      {review.reply ? (
        <div className="mt-3 rounded bg-gray-50 p-3 text-sm">
          <span className="font-medium">사장님 답글</span>
          <p className="mt-1">{review.reply.content}</p>
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          <div className="flex gap-2">
            <select value={styleId} onChange={(e) => setStyleId(Number(e.target.value))} className="rounded border px-2 py-1 text-sm">
              {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <button onClick={generate} className="rounded bg-blue-600 px-3 py-1 text-sm text-white">답글 생성</button>
          </div>
          {draft && (
            <div className="space-y-2">
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} className="w-full rounded border p-2 text-sm" />
              <button onClick={save} className="rounded bg-green-600 px-3 py-1 text-sm text-white">저장</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ReviewsPage() {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [styles, setStyles] = useState<Style[]>([]);
  const [filter, setFilter] = useState<"all" | "unanswered" | "answered">("unanswered");

  const load = useCallback(async () => {
    const qs = filter === "all" ? "" : `?status=${filter}`;
    setReviews(await apiGet<Review[]>(`/api/reviews${qs}`));
  }, [filter]);

  useEffect(() => { apiGet<Style[]>("/api/reply-styles").then(setStyles); }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-xl font-bold">리뷰 답글</h1>
      <div className="mt-3 flex gap-2 text-sm">
        {(["unanswered", "answered", "all"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`rounded px-3 py-1 ${filter === f ? "bg-black text-white" : "border"}`}>
            {f === "unanswered" ? "답글 대기" : f === "answered" ? "답글 완료" : "전체"}
          </button>
        ))}
      </div>
      <div className="mt-4 grid gap-3">
        {reviews.map((r) => <ReviewCard key={r.id} review={r} styles={styles} onSaved={load} />)}
        {reviews.length === 0 && <p className="text-sm text-gray-400">리뷰가 없습니다.</p>}
      </div>
    </main>
  );
}
