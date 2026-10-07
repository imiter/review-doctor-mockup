"use client";

import { useEffect, useRef, useState } from "react";
import { Card } from "@/components/Card";
import { apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
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

type OnboardingScenario = {
  id: number;
  category: string;
  virtual_review_text: string;
  draft_text: string;
  status: string;
};

type StylePrinciple = {
  category: string;
  label: string;
  rules: string;
  generated_from_count: number;
  updated_at: string;
};

type AllStylePrinciple = StylePrinciple & { needs_confirmation: boolean };

const ONBOARDING_CATEGORY_LABEL: Record<string, string> = {
  food_quality: "음식 품질(맛/온도/양)",
  delivery: "배달(지연/파손)",
  hygiene: "위생/이물질",
  service: "응대",
  price: "가격",
  missing_or_wrong_item: "오배송/누락",
};

function OnboardingTrainingCard({ storeId }: { storeId: number }) {
  const [scenarios, setScenarios] = useState<OnboardingScenario[] | null>(null);
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    apiGet<OnboardingScenario[]>(`/reply-onboarding/today?store_id=${storeId}`)
      .then(setScenarios)
      .catch(() => setScenarios([]));
  }, [storeId]);

  const current = scenarios?.[index] ?? null;

  useEffect(() => {
    if (current) setDraft(current.draft_text);
  }, [current]);

  // 글자 수에 맞춰 textarea 높이를 자동으로 늘린다 — 스크롤이나 수동 리사이즈 없이
  // 답글 전체가 한 번에 보이게. 위아래로 여유를 조금 더 둔다(+24px).
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 24}px`;
  }, [draft]);

  if (!scenarios || scenarios.length === 0 || !current) return null;

  const advance = () => {
    if (index + 1 < scenarios.length) {
      setIndex(index + 1);
    } else {
      setScenarios([]);
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await apiPost(`/reply-onboarding/scenarios/${current.id}/answer`, { content: draft });
      advance();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  const skip = async () => {
    setSaving(true);
    setError(null);
    try {
      await apiPost(`/reply-onboarding/scenarios/${current.id}/skip`);
      advance();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "건너뛰기에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title={`사장님 말투 학습 (${index + 1}/${scenarios.length})`}>
      <p className="mb-3 rounded-lg bg-surface-2 p-3 text-xs text-muted">
        아직 &quot;{ONBOARDING_CATEGORY_LABEL[current.category] ?? current.category}&quot; 유형의 실제 답글이 없어요.
        아래 예시 리뷰에 답글을 다듬어 저장하면, 이후 비슷한 리뷰에 사장님 말투로 답글이 생성돼요.
      </p>
      <p className="mb-2 text-sm text-foreground">{current.virtual_review_text}</p>
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={saving}
        className="w-full resize-none overflow-hidden rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
      />
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      <div className="mt-3 flex justify-end gap-2">
        <button
          onClick={skip}
          disabled={saving}
          className="rounded-lg px-4 py-2 text-sm text-muted transition hover:bg-surface-2 disabled:opacity-60"
        >
          건너뛰기
        </button>
        <button
          onClick={save}
          disabled={saving || !draft.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
        >
          저장
        </button>
      </div>
    </Card>
  );
}

function PrincipleReviewCard({ storeId }: { storeId: number }) {
  const [principles, setPrinciples] = useState<StylePrinciple[] | null>(null);
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    apiGet<{ principles: StylePrinciple[] }>(`/style-principles?store_id=${storeId}`)
      .then((r) => setPrinciples(r.principles))
      .catch(() => setPrinciples([]));
  }, [storeId]);

  const current = principles?.[index] ?? null;

  useEffect(() => {
    if (current) setDraft(current.rules);
  }, [current]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 24}px`;
  }, [draft]);

  if (!principles || principles.length === 0 || !current) return null;

  const advance = () => {
    if (index + 1 < principles.length) {
      setIndex(index + 1);
    } else {
      setPrinciples([]);
    }
  };

  const confirm = async () => {
    setSaving(true);
    setError(null);
    try {
      await apiPost(`/style-principles/${current.category}/confirm?store_id=${storeId}`, { rules: draft });
      advance();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "확인에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title={`AI 원칙 확인 (${index + 1}/${principles.length})`}>
      <p className="mb-3 rounded-lg bg-surface-2 p-3 text-xs text-muted">
        &quot;{current.label}&quot; 유형 답글에서 AI가 파악한 사장님 말투 원칙이 바뀌었어요
        (실제 답글 {current.generated_from_count}건 기준). 맞는지 확인하거나, 틀린 부분이 있으면
        고쳐서 저장해주세요 — 새 원칙은 이미 답글 생성에 적용되고 있어요.
      </p>
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={saving}
        className="w-full resize-none overflow-hidden rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
      />
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      <div className="mt-3 flex justify-end gap-2">
        <button
          onClick={confirm}
          disabled={saving || !draft.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
        >
          확인
        </button>
      </div>
    </Card>
  );
}

function AllPrinciplesCard({ storeId }: { storeId: number }) {
  const [principles, setPrinciples] = useState<AllStylePrinciple[] | null>(null);
  const [openCategory, setOpenCategory] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    apiGet<{ principles: AllStylePrinciple[] }>(`/style-principles/all?store_id=${storeId}`)
      .then((r) => setPrinciples(r.principles))
      .catch(() => setPrinciples([]));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeId]);

  const openEdit = (p: AllStylePrinciple) => {
    setOpenCategory(p.category);
    setDraft(p.rules);
    setError(null);
  };

  const save = async (category: string) => {
    setSaving(true);
    setError(null);
    try {
      await apiPost(`/style-principles/${category}/confirm?store_id=${storeId}`, { rules: draft });
      setOpenCategory(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  if (!principles) return null;

  return (
    <Card title="AI 원칙 전체 보기">
      <p className="mb-3 text-xs text-muted">
        카테고리별로 AI가 파악한 사장님 말투 원칙이에요. &quot;확인 대기&quot; 표시가 없어도
        아무 때나 열어서 다시 고칠 수 있어요.
      </p>
      {principles.length === 0 ? (
        <p className="text-sm text-muted">아직 생성된 원칙이 없습니다.</p>
      ) : (
        <div className="space-y-2">
          {principles.map((p) => (
            <div key={p.category} className="rounded-lg border border-border-subtle bg-surface-2">
              <button
                onClick={() => (openCategory === p.category ? setOpenCategory(null) : openEdit(p))}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm"
              >
                <span className="text-foreground">{p.label}</span>
                <span className="flex items-center gap-2 text-xs text-muted">
                  {p.needs_confirmation && (
                    <span className="rounded bg-accent-soft px-1.5 py-0.5 text-accent">확인 대기</span>
                  )}
                  {openCategory === p.category ? "접기" : "수정"}
                </span>
              </button>
              {openCategory === p.category && (
                <div className="border-t border-border-subtle p-3">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    disabled={saving}
                    rows={6}
                    className="w-full resize-none rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
                  />
                  {error && <p className="mt-2 text-xs text-danger">{error}</p>}
                  <div className="mt-2 flex justify-end gap-2">
                    <button
                      onClick={() => setOpenCategory(null)}
                      disabled={saving}
                      className="rounded-lg px-4 py-2 text-sm text-muted transition hover:bg-surface disabled:opacity-60"
                    >
                      취소
                    </button>
                    <button
                      onClick={() => save(p.category)}
                      disabled={saving || !draft.trim()}
                      className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                    >
                      저장
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

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
      {storeId && <OnboardingTrainingCard storeId={storeId} />}
      {storeId && <PrincipleReviewCard storeId={storeId} />}

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

      {storeId && <AllPrinciplesCard storeId={storeId} />}
    </div>
  );
}
