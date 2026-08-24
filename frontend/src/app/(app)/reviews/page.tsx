"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { Card } from "@/components/Card";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import { useStoreContext } from "@/lib/store-context";

type ReplyStyle = { id: number; name: string; description: string };
type ReplyRef = { content: string; style_id: number | null } | null;
type SecondaryReply = { id: number; content: string; created_at: string };
type Review = {
  id: number;
  platform_name: string;
  platform_shop_no: string | null;
  menu_summary: string;
  rating: number;
  content: string;
  customer_nickname: string;
  customer_order_count: number;
  image_urls: string[];
  status: "unanswered" | "pending" | "answered";
  category: string;
  is_sensitive: boolean;
  created_at: string;
  final_reply: ReplyRef;
  draft_reply: ReplyRef;
  secondary_replies: SecondaryReply[];
};
type Brand = { shop_no: string; shop_name: string };

const CATEGORY_LABELS: Record<string, string> = {
  food_quality: "음식 품질",
  delivery: "배달",
  hygiene: "위생",
  service: "응대",
  price: "가격",
  missing_or_wrong_item: "오배송/누락",
};

const FILTERS = [
  { key: "unanswered", label: "답글 대기" },
  { key: "pending", label: "검토 중" },
  { key: "answered", label: "답글 완료" },
  { key: "", label: "전체" },
] as const;

const pad = (n: number) => String(n).padStart(2, "0");
const toDateStr = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// 배민 사장님광장의 "오늘/어제/최근 1주일/최근 1개월/지난주/지난달" 프리셋을
// 참고해 우리 화면에 맞게 재구성했다(화면을 그대로 복제하지 않음).
const DATE_PRESETS: { label: string; range: () => [string, string] }[] = [
  {
    label: "오늘",
    range: () => {
      const t = toDateStr(new Date());
      return [t, t];
    },
  },
  {
    label: "어제",
    range: () => {
      const d = new Date();
      d.setDate(d.getDate() - 1);
      const t = toDateStr(d);
      return [t, t];
    },
  },
  {
    label: "최근 1주일",
    range: () => {
      const to = new Date();
      const from = new Date();
      from.setDate(from.getDate() - 6);
      return [toDateStr(from), toDateStr(to)];
    },
  },
  {
    label: "최근 1개월",
    range: () => {
      const to = new Date();
      const from = new Date();
      from.setMonth(from.getMonth() - 1);
      return [toDateStr(from), toDateStr(to)];
    },
  },
  {
    label: "지난주",
    range: () => {
      const today = new Date();
      const mondayOffset = today.getDay() === 0 ? 6 : today.getDay() - 1;
      const thisMonday = new Date(today);
      thisMonday.setDate(today.getDate() - mondayOffset);
      const lastMonday = new Date(thisMonday);
      lastMonday.setDate(thisMonday.getDate() - 7);
      const lastSunday = new Date(thisMonday);
      lastSunday.setDate(thisMonday.getDate() - 1);
      return [toDateStr(lastMonday), toDateStr(lastSunday)];
    },
  },
  {
    label: "지난달",
    range: () => {
      const today = new Date();
      const firstOfThisMonth = new Date(today.getFullYear(), today.getMonth(), 1);
      const lastOfPrevMonth = new Date(firstOfThisMonth);
      lastOfPrevMonth.setDate(lastOfPrevMonth.getDate() - 1);
      const firstOfPrevMonth = new Date(lastOfPrevMonth.getFullYear(), lastOfPrevMonth.getMonth(), 1);
      return [toDateStr(firstOfPrevMonth), toDateStr(lastOfPrevMonth)];
    },
  },
];

type SavedDraft = { mode: "manual" | "ai"; draft: string };

function loadSavedDraft(reviewId: number): SavedDraft | null {
  try {
    const raw = sessionStorage.getItem(`review-draft-${reviewId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && (parsed.mode === "manual" || parsed.mode === "ai") && typeof parsed.draft === "string") {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

function saveDraftToStorage(reviewId: number, mode: "idle" | "manual" | "ai", draft: string) {
  try {
    if (mode === "idle") {
      sessionStorage.removeItem(`review-draft-${reviewId}`);
    } else {
      sessionStorage.setItem(`review-draft-${reviewId}`, JSON.stringify({ mode, draft }));
    }
  } catch {
    // sessionStorage 사용 불가(프라이빗 브라우징 등) — 초안 보존은 best-effort이므로 조용히 무시
  }
}

function clearSavedDraft(reviewId: number) {
  try {
    sessionStorage.removeItem(`review-draft-${reviewId}`);
  } catch {
    // ignore
  }
}

function ReviewCard({
  review, styles, onSaved, brandName,
}: {
  review: Review; styles: ReplyStyle[]; onSaved: () => void; brandName?: string;
}) {
  const { refreshBilling } = useStoreContext();
  const savedDraft = loadSavedDraft(review.id);
  const [mode, setMode] = useState<"idle" | "manual" | "ai">(
    savedDraft?.mode ?? (review.draft_reply ? "ai" : "idle")
  );
  const [styleId, setStyleId] = useState(review.draft_reply?.style_id ?? styles[0]?.id ?? 0);
  const [draft, setDraft] = useState(savedDraft?.draft ?? review.draft_reply?.content ?? "");
  const [lastGenerated, setLastGenerated] = useState(review.draft_reply?.content ?? "");
  const [toneOverridden, setToneOverridden] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [secondaryText, setSecondaryText] = useState("");
  const [savingSecondary, setSavingSecondary] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  useEffect(() => {
    if (styles.length > 0 && styleId === 0) setStyleId(styles[0].id);
  }, [styles, styleId]);

  useEffect(() => {
    saveDraftToStorage(review.id, mode, draft);
  }, [review.id, mode, draft]);

  const generate = async () => {
    setGenerating(true);
    setGenerateError(null);
    try {
      const res = await apiPost<{ content: string; tone_overridden: boolean }>(
        `/reviews/${review.id}/generate-reply`, { style_id: styleId }
      );
      setDraft(res.content);
      setLastGenerated(res.content);
      setToneOverridden(res.tone_overridden);
      setMode("ai");
      // onSaved()를 여기서 부르지 않는다 — 목록을 새로고침하면 이 리뷰가
      // 서버에서 이미 pending으로 바뀌어 "답글 대기" 필터에서 사라지고,
      // 사장님이 미리보기를 확인·수정하기도 전에 카드가 없어져 버린다.
      // 목록 갱신은 최종 "답글 등록"을 눌렀을 때만 한다.
      await refreshBilling();
    } catch (e) {
      if (e instanceof ApiError && e.errorCode === "reply_limit_exceeded") {
        setGenerateError(e.message);
      } else {
        setGenerateError(e instanceof ApiError ? e.message : "답글 생성에 실패했습니다.");
      }
    } finally {
      setGenerating(false);
    }
  };

  const regenerate = async () => {
    // 마지막 생성 결과와 지금 텍스트가 다르면(직접 손을 댄 흔적이 있으면)
    // 되돌릴 방법이 없으니 실수로 날리지 않도록 한 번 확인한다.
    if (draft !== lastGenerated) {
      const confirmed = window.confirm("지금까지 수정한 내용이 사라집니다. 다시 생성할까요?");
      if (!confirmed) return;
    }
    await generate();
  };

  const startManual = () => {
    setGenerateError(null);
    setMode("manual");
    setDraft("");
  };

  const cancelDraft = () => {
    setMode("idle");
    setDraft("");
    setGenerateError(null);
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPost(`/reviews/${review.id}/reply`, { style_id: mode === "ai" ? styleId : null, content: draft });
      clearSavedDraft(review.id);
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const saveSecondary = async () => {
    if (!secondaryText.trim()) return;
    setSavingSecondary(true);
    try {
      await apiPost(`/reviews/${review.id}/secondary-reply`, { content: secondaryText });
      setSecondaryText("");
      onSaved();
    } finally {
      setSavingSecondary(false);
    }
  };

  return (
    <div className="rounded-2xl border border-border-subtle bg-surface-2 p-6 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-lg bg-surface px-2.5 py-1 text-xs font-medium text-accent">{review.platform_name}</span>
        {review.category !== "no_issue" && (
          <span
            className={`rounded-lg bg-surface px-2.5 py-1 text-xs font-medium ${
              review.is_sensitive ? "text-danger" : "text-warning"
            }`}
          >
            {review.is_sensitive ? "⚠ " : ""}
            {CATEGORY_LABELS[review.category] ?? review.category}
          </span>
        )}
        <span className="ml-auto text-xs text-muted">{new Date(review.created_at).toLocaleString("ko-KR")}</span>
      </div>

      {brandName && <p className="mt-3 text-xs text-muted">{brandName}</p>}

      <div className={`flex items-start gap-5 ${brandName ? "mt-1.5" : "mt-4"}`}>
        <div className="w-52 flex-shrink-0 space-y-1.5">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-base font-semibold text-foreground">{review.customer_nickname}</span>
            <span className="text-xs text-muted">{review.customer_order_count}회 주문</span>
          </div>
          <div className="text-sm text-warning">
            {"★".repeat(review.rating)}
            <span className="text-border-subtle">{"★".repeat(5 - review.rating)}</span>
          </div>
          <span className="inline-block rounded-lg bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent">
            {review.menu_summary}
          </span>
        </div>
        <div className="flex-1 space-y-3 rounded-xl bg-surface p-4">
          {review.image_urls.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {review.image_urls.map((url) => (
                <a key={url} href={url} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-xl border border-border-subtle">
                  <Image
                    src={url}
                    alt="고객이 첨부한 리뷰 사진"
                    width={112}
                    height={112}
                    className="h-28 w-28 object-cover transition hover:opacity-90"
                  />
                </a>
              ))}
            </div>
          )}
          <p className="text-sm leading-relaxed text-foreground">{review.content}</p>
        </div>
      </div>

      {review.final_reply ? (
        <div className="mt-5 space-y-3">
          <div className="rounded-xl border border-border-subtle bg-surface p-4">
            <p className="mb-1.5 text-xs font-medium text-success">등록된 답글</p>
            <p className="text-sm leading-relaxed text-foreground">{review.final_reply.content}</p>
          </div>
          {review.secondary_replies.map((r) => (
            <div key={r.id} className="rounded-xl border border-accent/30 bg-accent-soft p-4">
              <p className="mb-1.5 text-xs font-medium text-accent">2차 답글</p>
              <p className="text-sm leading-relaxed text-foreground">{r.content}</p>
            </div>
          ))}
          <div className="flex gap-2">
            <input
              value={secondaryText}
              onChange={(e) => setSecondaryText(e.target.value)}
              placeholder="추가로 안내할 내용을 입력하세요"
              className="flex-1 rounded-xl border border-border-subtle bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-accent"
            />
            <button
              onClick={saveSecondary}
              disabled={savingSecondary || !secondaryText.trim()}
              className="rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {savingSecondary ? "등록 중..." : "2차 답글 등록"}
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-5 space-y-2.5">
          {mode === "idle" && !generateError && (
            <div className="flex flex-col gap-2.5">
              <button
                onClick={startManual}
                className="w-full rounded-xl border border-border-subtle py-3 text-sm font-medium text-muted transition hover:border-foreground hover:text-foreground"
              >
                ✏️ 직접 답글 쓰기
              </button>
              <button
                onClick={generate}
                disabled={generating || styles.length === 0}
                className="w-full rounded-xl bg-accent py-3 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {generating ? "생성 중..." : "✨ AI 추천 답글 보기"}
              </button>
            </div>
          )}

          {generateError && (
            <div className="space-y-2.5">
              <p className="text-sm text-danger">
                {generateError}{" "}
                <Link href="/account/billing" className="underline">
                  구독 관리
                </Link>
              </p>
              {mode === "idle" && (
                <button
                  onClick={startManual}
                  className="w-full rounded-xl border border-border-subtle py-3 text-sm font-medium text-muted transition hover:border-foreground hover:text-foreground"
                >
                  대신 직접 답글 쓰기
                </button>
              )}
            </div>
          )}

          {mode === "ai" && (
            <div className="space-y-3 rounded-xl border border-accent/40 bg-accent-soft/40 p-4">
              {toneOverridden && (
                <p className="rounded-lg bg-warning/10 px-3 py-1.5 text-xs text-warning">
                  ⚠ 민감한 리뷰라 자동으로 진중한 톤으로 작성돼요
                </p>
              )}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-accent">AI 추천 답글</p>
                <div className="flex items-center gap-2">
                  <select
                    value={styleId}
                    onChange={(e) => setStyleId(Number(e.target.value))}
                    className="rounded-lg border border-border-subtle bg-surface px-2.5 py-1.5 text-xs outline-none focus:border-accent"
                  >
                    {styles.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                  <button
                    onClick={regenerate}
                    disabled={generating}
                    title="다시 생성"
                    className="rounded-lg border border-border-subtle p-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                  >
                    {generating ? "..." : "↻"}
                  </button>
                </div>
              </div>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={4}
                className="w-full rounded-xl border border-border-subtle bg-surface p-3.5 text-sm leading-relaxed outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2.5">
                <button
                  onClick={save}
                  disabled={saving || !draft.trim()}
                  className="flex-1 rounded-xl bg-success py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "등록 중..." : "이대로 답글 등록"}
                </button>
                <button
                  onClick={cancelDraft}
                  disabled={saving}
                  className="rounded-xl border border-border-subtle px-4 py-2.5 text-sm text-muted transition hover:text-foreground disabled:opacity-50"
                >
                  취소
                </button>
              </div>
            </div>
          )}

          {mode === "manual" && (
            <div className="space-y-3 rounded-xl border border-border-subtle bg-surface p-4">
              <p className="text-sm font-medium text-muted">직접 작성</p>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={4}
                autoFocus
                placeholder="답글을 입력하세요"
                className="w-full rounded-xl border border-border-subtle bg-surface-2 p-3.5 text-sm leading-relaxed outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2.5">
                <button
                  onClick={save}
                  disabled={saving || !draft.trim()}
                  className="flex-1 rounded-xl bg-success py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "등록 중..." : "이대로 답글 등록"}
                </button>
                <button
                  onClick={cancelDraft}
                  disabled={saving}
                  className="rounded-xl border border-border-subtle px-4 py-2.5 text-sm text-muted transition hover:text-foreground disabled:opacity-50"
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
  const { storeId, billing } = useStoreContext();
  const [reviews, setReviews] = useState<Review[]>([]);
  const [styles, setStyles] = useState<ReplyStyle[]>([]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("unanswered");
  const [brands, setBrands] = useState<Brand[]>([]);
  const [selectedShopNo, setSelectedShopNo] = useState(""); // "" = 전체 브랜드
  const [dateFrom, setDateFrom] = useState(""); // "" = 시작일 제한 없음
  const [dateTo, setDateTo] = useState(""); // "" = 종료일 제한 없음
  const [activePreset, setActivePreset] = useState<string | null>(null);

  const applyPreset = (preset: (typeof DATE_PRESETS)[number]) => {
    const [from, to] = preset.range();
    setDateFrom(from);
    setDateTo(to);
    setActivePreset(preset.label);
  };
  const clearDateRange = () => {
    setDateFrom("");
    setDateTo("");
    setActivePreset(null);
  };

  const isDateRangeActive = Boolean(dateFrom || dateTo);

  const load = useCallback(async () => {
    if (!storeId) return;
    // 기간을 지정했을 때는 답글 대기/완료 탭과 무관하게 그 기간의 리뷰를 전부
    // 보여준다 — 이미 답글 단 것도 같이 봐야 기간 안에서 한눈에 확인 가능하다.
    const effectiveStatus = isDateRangeActive ? "" : filter;
    const qs = effectiveStatus ? `&status=${effectiveStatus}` : "";
    const brandQs = selectedShopNo ? `&platform_shop_no=${selectedShopNo}` : "";
    const dateQs = `${dateFrom ? `&date_from=${dateFrom}` : ""}${dateTo ? `&date_to=${dateTo}` : ""}`;
    setReviews(await apiGet<Review[]>(`/reviews?store_id=${storeId}${qs}${brandQs}${dateQs}`));
  }, [storeId, filter, isDateRangeActive, selectedShopNo, dateFrom, dateTo]);

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
        <p className="text-sm text-muted">칭찬 리뷰는 스타일 템플릿으로, 불만 리뷰는 실제 AI가 사장님 말투를 학습해 답글을 생성해요</p>
        {billing && (
          <p className="text-xs text-muted">
            오늘 답글 생성{" "}
            {billing.is_pro ? "무제한 (Pro)" : `${billing.replies_used_today}/${billing.daily_reply_limit} (Basic)`}
          </p>
        )}
      </div>

      <Card>
        <div className="space-y-4">
          {brands.length > 1 && (
            <div>
              <label className="mb-1 block text-xs text-muted">브랜드(매장) 선택</label>
              <select
                value={selectedShopNo}
                onChange={(e) => setSelectedShopNo(e.target.value)}
                className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
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

          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label className="mb-1 block text-xs text-muted">시작일</label>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(e.target.value);
                  setActivePreset(null);
                }}
                max={dateTo || undefined}
                className="rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              />
            </div>
            <span className="pb-2.5 text-muted">~</span>
            <div>
              <label className="mb-1 block text-xs text-muted">종료일</label>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => {
                  setDateTo(e.target.value);
                  setActivePreset(null);
                }}
                min={dateFrom || undefined}
                className="rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent"
              />
            </div>
            {(dateFrom || dateTo) && (
              <button
                onClick={clearDateRange}
                className="rounded-lg px-3 py-2 text-xs text-muted transition hover:text-foreground"
              >
                기간 초기화
              </button>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            {DATE_PRESETS.map((preset) => (
              <button
                key={preset.label}
                onClick={() => applyPreset(preset)}
                className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                  activePreset === preset.label
                    ? "border-accent bg-accent-soft text-accent"
                    : "border-border-subtle text-muted hover:text-foreground"
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
      </Card>

      <div>
        <div className="flex gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              disabled={isDateRangeActive}
              title={isDateRangeActive ? "기간 선택 중에는 모든 상태의 리뷰를 함께 보여줍니다" : undefined}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${
                !isDateRangeActive && filter === f.key
                  ? "bg-accent text-white"
                  : "border border-border-subtle text-muted hover:text-foreground"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        {isDateRangeActive && (
          <p className="mt-2 text-xs text-muted">기간 선택 중에는 답글 대기/완료 상태와 무관하게 해당 기간의 모든 리뷰를 보여줍니다.</p>
        )}
      </div>

      {reviews.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">해당하는 리뷰가 없습니다.</p>
        </Card>
      ) : (
        <div className="space-y-5">
          {reviews.map((r) => (
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
          ))}
        </div>
      )}
    </div>
  );
}
