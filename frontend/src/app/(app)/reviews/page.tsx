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
  platform_shop_no: string | null;
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
type Brand = { shop_no: string; shop_name: string };

const FILTERS = [
  { key: "unanswered", label: "답글 대기" },
  { key: "pending", label: "검토 중" },
  { key: "answered", label: "답글 완료" },
  { key: "", label: "전체" },
] as const;

function ReviewCard({
  review, styles, onSaved, brandName,
}: {
  review: Review; styles: ReplyStyle[]; onSaved: () => void; brandName?: string;
}) {
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
      // onSaved()를 여기서 부르지 않는다 — 목록을 새로고침하면 이 리뷰가
      // 서버에서 이미 pending으로 바뀌어 "답글 대기" 필터에서 사라지고,
      // 사장님이 미리보기를 확인·수정하기도 전에 카드가 없어져 버린다.
      // 목록 갱신은 최종 "답글 등록"을 눌렀을 때만 한다.
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
        {brandName && (
          <span className="rounded bg-surface px-2 py-0.5 font-medium text-foreground">{brandName}</span>
        )}
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
            <div className="space-y-2 rounded-lg border border-accent/40 bg-accent-soft/40 p-3">
              <p className="text-xs font-medium text-accent">미리보기 — 등록 전 자유롭게 수정하세요</p>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-border-subtle bg-surface p-2.5 text-sm outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={save}
                  disabled={saving || !draft.trim()}
                  className="rounded-lg bg-success px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "등록 중..." : "이대로 답글 등록"}
                </button>
                <button
                  onClick={() => setDraft("")}
                  disabled={saving}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                >
                  취소
                </button>
              </div>
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
  const [brands, setBrands] = useState<Brand[]>([]);
  const [selectedShopNo, setSelectedShopNo] = useState(""); // "" = 전체 브랜드

  const load = useCallback(async () => {
    if (!storeId) return;
    const qs = filter ? `&status=${filter}` : "";
    const brandQs = selectedShopNo ? `&platform_shop_no=${selectedShopNo}` : "";
    setReviews(await apiGet<Review[]>(`/reviews?store_id=${storeId}${qs}${brandQs}`));
  }, [storeId, filter, selectedShopNo]);

  useEffect(() => {
    apiGet<ReplyStyle[]>("/reply-styles").then(setStyles);
  }, []);
  useEffect(() => {
    // 배민처럼 한 연결에 여러 브랜드(매장)가 있는 경우에만 의미가 있다 —
    // Mock 연결이나 단일 매장 계정은 빈 배열이 오고, 이 경우 드롭다운 자체를
    // 렌더링하지 않는다(아래 JSX의 brands.length > 1 조건).
    if (!storeId) return;
    apiGet<Brand[]>(`/store-connections/baemin/shops?store_id=${storeId}`).then(setBrands);
  }, [storeId]);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">리뷰 관리</h1>
        <p className="text-sm text-muted">답글 생성은 스타일 템플릿 기반 Mock — 실제 AI 호출 없음</p>
      </div>

      {brands.length > 1 && (
        <div>
          <label className="mb-1 block text-xs text-muted">브랜드(매장) 선택</label>
          <select
            value={selectedShopNo}
            onChange={(e) => setSelectedShopNo(e.target.value)}
            className="w-full max-w-sm rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
          >
            <option value="">전체 브랜드 ({brands.length}개)</option>
            {brands.map((b) => (
              <option key={b.shop_no} value={b.shop_no}>
                {b.shop_name}
              </option>
            ))}
          </select>
        </div>
      )}

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
            reviews.map((r) => (
              <ReviewCard
                key={r.id}
                review={r}
                styles={styles}
                onSaved={load}
                brandName={
                  brands.length > 1
                    ? brands.find((b) => b.shop_no === r.platform_shop_no)?.shop_name
                    : undefined
                }
              />
            ))
          )}
        </div>
      </Card>
    </div>
  );
}
