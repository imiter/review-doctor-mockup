"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

type RunRow = {
  trace_id: string;
  started_at: string | null;
  status: string;
  category_label: string | null;
  passed_verification: boolean | null;
  retry_count: number | null;
  final_content_preview: string;
};

type RunNode = {
  name: string;
  run_type: string;
  status: string;
  start_time: string | null;
  latency_ms: number | null;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
};

type RunDetail = { trace_id: string; nodes: RunNode[] };

type AccuracyCategory = { category: string; label: string; avg_similarity: number; sample_count: number };

type ReviewPreview = {
  content: string;
  rating: number;
  customer_nickname: string;
  category: string;
  is_sensitive: boolean;
  sentiment_conflict: boolean;
};

type ExamplePreview = { category: string; source: string; review_text: string; reply_text: string };

type CopyPasteMatchPreview = { review_text: string; reply_text: string } | null;

const NODE_NAME_LABEL: Record<string, string> = {
  retrieve_memory: "기억 조회",
  generate_draft: "초안 생성",
  ChatAnthropic: "Claude Sonnet 호출",
  verify_draft: "검증",
  route_after_verify: "분기 판단",
  fix_draft: "수정(재시도)",
  finalize: "최종화",
};

const PIPELINE_NODES: { key: string; title: string; summary: string; touches: string }[] = [
  {
    key: "retrieve_memory",
    title: "① 기억 조회",
    summary: "리뷰 카테고리에 맞는 절차/의미/일화 기억을 전부 모은다.",
    touches: "procedural_rules · brand_menu_info · brand_ceo_notices · golden_examples(3단계 검색) · store_style_profile(카테고리별 원칙)",
  },
  {
    key: "generate_draft",
    title: "② 초안 생성",
    summary: "모은 기억을 시스템 프롬프트에 꽂아 Claude Sonnet을 호출한다.",
    touches: "ChatAnthropic(langchain-anthropic) — 시스템: 원칙+예시+메뉴정보 / 유저: 리뷰 본문",
  },
  {
    key: "verify_draft",
    title: "③ 검증",
    summary: "LLM 재판단 없이 결정론적 체크만 한다 — 말투가 무난하게 수렴하는 걸 막기 위해서다.",
    touches: "이모지 정규식 체크 · few-shot 예시와의 문자열 유사도(복붙 체크)",
  },
  {
    key: "finalize",
    title: "④ 최종화",
    summary: "검증을 통과했거나 재시도 상한(2회)에 닿으면 끝낸다.",
    touches: "최종 답글 텍스트 + 통과 여부(passed_verification) 반환",
  },
];

// 백엔드 _EMOJI_PATTERN과 완전히 동일할 필요는 없다 — 여기서는 "실제로
// 이모지가 섞여 있었다"는 걸 눈으로 바로 확인시키는 시각적 보조 용도다.
const EMOJI_SPLIT = /([\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2190}-\u{21FF}])/gu;
const EMOJI_TEST = /^[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2190}-\u{21FF}]$/u;

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function HighlightEmoji({ text }: { text: string }) {
  if (!text) return null;
  const parts = text.split(EMOJI_SPLIT);
  return (
    <>
      {parts.map((part, i) =>
        EMOJI_TEST.test(part) ? (
          // line-through은 이모지(컬러 글리프)에서 대부분 브라우저가 그려주지
          // 않아(실측 확인, 2026-10-08) 대신 배경 하이라이트로 표시한다 —
          // 글자 장식과 달리 배경색은 글리프 종류와 무관하게 항상 보인다.
          <span key={i} className="rounded bg-danger/25 px-0.5 ring-1 ring-danger/60">
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function NodeCard({
  title,
  summary,
  touches,
  variant = "default",
  example,
}: {
  title: string;
  summary: string;
  touches: string;
  variant?: "default" | "warning";
  example?: React.ReactNode;
}) {
  return (
    <div
      className={`h-full rounded-xl border p-3 ${
        variant === "warning" ? "border-warning/40 bg-warning/5" : "border-border-subtle bg-surface-2"
      }`}
    >
      <p className={`text-sm font-semibold ${variant === "warning" ? "text-warning" : "text-accent"}`}>{title}</p>
      <p className="mt-1 text-xs text-foreground">{summary}</p>
      <p className="mt-2 text-[11px] text-muted">{touches}</p>
      {example}
    </div>
  );
}

function ExampleSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mt-3 space-y-2 border-t border-border-subtle pt-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</p>
      {children}
    </div>
  );
}

function MemoryExample({ node }: { node: RunNode | undefined }) {
  if (!node) return null;
  const review = node.outputs.review_preview as ReviewPreview | undefined;
  const examples = (node.outputs.examples_preview as ExamplePreview[] | undefined) ?? [];
  return (
    <ExampleSection label="실제 예시">
      {review && (
        <div className="rounded-lg bg-surface px-2.5 py-2">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-muted">
              {review.customer_nickname} · 별점 {review.rating}
            </span>
            {review.sentiment_conflict && (
              <span className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning">별점-내용 불일치</span>
            )}
            {review.is_sensitive && (
              <span className="rounded bg-danger/15 px-1.5 py-0.5 text-[10px] text-danger">민감 리뷰</span>
            )}
          </div>
          <p className="text-xs text-foreground">&ldquo;{review.content}&rdquo;</p>
        </div>
      )}
      <p className="text-[11px] text-muted">
        {examples.length > 0 ? `과거 답글 ${examples.length}건을 참고함` : "참고할 과거 답글 없음(신규 매장이거나 데이터 부족)"}
      </p>
      {examples.map((ex, i) => (
        <div key={i} className="rounded-lg bg-surface px-2.5 py-1.5">
          <p className="text-[10px] text-accent">
            {ex.source} · {ex.category}
          </p>
          <p className="text-xs text-muted">
            &ldquo;{ex.review_text}&rdquo; → &ldquo;{ex.reply_text}&rdquo;
          </p>
        </div>
      ))}
    </ExampleSection>
  );
}

function DraftExample({ node }: { node: RunNode | undefined }) {
  if (!node) return null;
  const draft = asString(node.outputs.draft);
  return (
    <ExampleSection label="AI가 쓴 초안">
      <p className="rounded-lg bg-surface px-2.5 py-2 text-xs text-foreground">{draft}</p>
    </ExampleSection>
  );
}

function VerifyExample({ nodes }: { nodes: RunNode[] }) {
  if (nodes.length === 0) return null;
  return (
    <ExampleSection label="검증 시도">
      {nodes.map((node, i) => {
        const violations = asStringArray(node.outputs.violations);
        const draft = asString(node.inputs.draft);
        const match = (node.outputs.copy_paste_match_preview as CopyPasteMatchPreview) ?? null;
        return (
          <div key={i} className="rounded-lg bg-surface px-2.5 py-2">
            <div className="mb-1 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-medium text-foreground">{i + 1}차 검증</span>
              {violations.length === 0 ? (
                <span className="rounded bg-success/15 px-1.5 py-0.5 text-[10px] text-success">위반 없음</span>
              ) : (
                violations.map((v) => (
                  <span key={v} className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning">
                    {v === "emoji" ? "이모지 위반" : "복붙 의심"}
                  </span>
                ))
              )}
            </div>
            <p className="text-xs text-foreground">
              <HighlightEmoji text={draft} />
            </p>
            {match && (
              <div className="mt-1.5 rounded-lg border border-warning/30 bg-warning/5 px-2 py-1.5">
                <p className="text-[10px] text-warning">과거 답글과 유사도 높음 — 이 예시와 거의 똑같이 썼어요</p>
                <p className="text-xs text-muted">&ldquo;{match.reply_text}&rdquo;</p>
              </div>
            )}
          </div>
        );
      })}
    </ExampleSection>
  );
}

function FixExample({ nodes }: { nodes: RunNode[] }) {
  if (nodes.length === 0) {
    return (
      <ExampleSection label="실제 예시">
        <p className="text-xs text-muted">이번 실행에서는 재시도가 없었어요 — 처음부터 통과했습니다.</p>
      </ExampleSection>
    );
  }
  return (
    <ExampleSection label="수정 시도">
      {nodes.map((node, i) => (
        <div key={i} className="rounded-lg bg-surface px-2.5 py-2">
          <p className="mb-1 text-[11px] font-medium text-foreground">{i + 1}차 수정 결과</p>
          <p className="text-xs text-foreground">{asString(node.outputs.draft)}</p>
        </div>
      ))}
    </ExampleSection>
  );
}

function FinalizeExample({ node, retryCount }: { node: RunNode | undefined; retryCount: number | null }) {
  if (!node) return null;
  const finalContent = asString(node.outputs.final_content);
  const passed = node.outputs.passed_verification as boolean | undefined;
  return (
    <ExampleSection label="최종 답글">
      <div className="flex items-center gap-1.5">
        <StatusBadge passed={passed ?? null} />
        {retryCount != null && retryCount > 0 && <span className="text-[11px] text-muted">재시도 {retryCount}회</span>}
      </div>
      <p className="rounded-lg bg-surface px-2.5 py-2 text-xs text-foreground">{finalContent}</p>
    </ExampleSection>
  );
}

function StatusBadge({ passed }: { passed: boolean | null }) {
  if (passed === null) return <span className="rounded bg-surface-2 px-2 py-0.5 text-[11px] text-muted">알 수 없음</span>;
  return passed ? (
    <span className="rounded bg-success/15 px-2 py-0.5 text-[11px] font-medium text-success">통과</span>
  ) : (
    <span className="rounded bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning">보류</span>
  );
}

function NodeIO({ title, data }: { title: string; data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return (
      <div>
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">{title}</p>
        <p className="text-xs text-muted">없음</p>
      </div>
    );
  }
  return (
    <div>
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">{title}</p>
      <div className="space-y-1.5">
        {entries.map(([key, value]) => (
          <div key={key} className="rounded-lg bg-surface px-2.5 py-1.5">
            <p className="text-[11px] text-accent">{key}</p>
            <p className="whitespace-pre-wrap break-words text-xs text-foreground">
              {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function useRunDetail(traceId: string | null) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    if (!traceId) return;
    apiGet<RunDetail>(`/admin/llmops/runs/${traceId}`)
      .then(setDetail)
      .catch(() => setError("LangSmith에서 이 트레이스를 찾을 수 없어요(보관 기간이 지났거나 설정 문제일 수 있어요)."));
  }, [traceId]);

  return { detail, error };
}

function RunDetailPanel({ trace_id }: { trace_id: string }) {
  const { detail, error } = useRunDetail(trace_id);

  if (error) return <p className="px-4 py-3 text-xs text-danger">{error}</p>;
  if (!detail) return <p className="px-4 py-3 text-xs text-muted">노드별 상세 불러오는 중...</p>;

  return (
    <div className="space-y-3 border-t border-border-subtle bg-surface/60 p-4">
      {detail.nodes.map((node, i) => (
        <div key={i} className="rounded-xl border border-border-subtle bg-surface-2 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium text-foreground">
              {NODE_NAME_LABEL[node.name] ?? node.name}
              <span className="ml-2 text-xs text-muted">({node.name})</span>
            </p>
            <p className="text-[11px] text-muted">{node.latency_ms != null ? `${node.latency_ms}ms` : ""}</p>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <NodeIO title="입력" data={node.inputs} />
            <NodeIO title="출력" data={node.outputs} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function AdminLlmopsPage() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracyCategory[] | null>(null);
  const [openTraceId, setOpenTraceId] = useState<string | null>(null);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ runs: RunRow[] }>("/admin/llmops/runs?limit=20").then((r) => setRuns(r.runs));
    apiGet<{ categories: AccuracyCategory[] }>("/admin/llmops/accuracy").then((r) => setAccuracy(r.categories));
  }, []);

  useEffect(() => {
    if (runs && runs.length > 0 && selectedTraceId === null) {
      setSelectedTraceId(runs[0].trace_id);
    }
  }, [runs, selectedTraceId]);

  const { detail, error: detailError } = useRunDetail(selectedTraceId);
  const selectedRun = runs?.find((r) => r.trace_id === selectedTraceId) ?? null;

  // detail이 아직 안 불러와졌으면(또는 실행 이력 자체가 없으면) 어떤 카드도
  // "실제 예시"를 보여주면 안 된다 — 특히 ⑤수정(재시도)는 "이번 실행에서는
  // 재시도가 없었어요"라는 정상적인 빈 상태 문구를 갖고 있어서, detail 로딩
  // 중에도 fixNodes가 빈 배열이라는 이유만으로 그 문구가 먼저 잘못 깜빡이는
  // 문제가 있었다(2026-10-08 실측 확인) — detail이 실제로 준비된 뒤에만
  // 노드 조회를 해서 이 깜빡임을 없앤다.
  const examplesReady = detail !== null;
  const nodesNamed = (name: string) => (detail?.nodes.filter((n) => n.name === name) ?? []);
  const retrieveNode = examplesReady ? nodesNamed("retrieve_memory")[0] : undefined;
  const draftNode = examplesReady ? nodesNamed("generate_draft")[0] : undefined;
  const verifyNodes = examplesReady ? nodesNamed("verify_draft") : [];
  const fixNodes = examplesReady ? nodesNamed("fix_draft") : [];
  const finalizeNode = examplesReady ? nodesNamed("finalize")[0] : undefined;

  const selectRun = (trace_id: string) => {
    setSelectedTraceId(trace_id);
    setOpenTraceId((prev) => (prev === trace_id ? prev : trace_id));
  };

  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h1 className="text-lg font-semibold">LLMOps — 답글 생성 파이프라인</h1>
        <p className="text-xs text-muted">
          리뷰 답글이 실제로 어떤 노드를 거쳐, 어떤 데이터를 참조해서 만들어지는지 추적합니다.
        </p>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-surface p-5">
        <h2 className="mb-1 text-sm font-semibold text-foreground">노드 구성도</h2>
        <p className="mb-1 text-[11px] text-muted">
          리뷰 한 건이 답글로 나오기까지 거치는 노드와, 노드 사이에 오가는 데이터입니다.
        </p>
        {runs === null ? (
          <p className="mb-4 text-[11px] text-muted">예시 불러오는 중...</p>
        ) : runs.length === 0 ? (
          <p className="mb-4 text-[11px] text-muted">
            아직 답글 생성 이력이 없어서 실제 예시를 보여줄 수 없어요 — 리뷰 답글을 한 번 생성하면 여기 채워집니다.
          </p>
        ) : detailError ? (
          <p className="mb-4 text-[11px] text-danger">{detailError}</p>
        ) : !detail ? (
          <p className="mb-4 text-[11px] text-muted">예시 불러오는 중...</p>
        ) : (
          <p className="mb-4 text-[11px] text-accent">
            표시 중인 예시: {selectedRun?.category_label ?? "—"} ·{" "}
            {selectedRun?.started_at ? new Date(selectedRun.started_at).toLocaleString("ko-KR") : ""}
            {" — 아래 "}
            <span className="text-muted">최근 실행 이력</span>에서 다른 사례를 고를 수 있어요.
          </p>
        )}
        <div className="overflow-x-auto">
          <div
            className="grid min-w-[900px] gap-x-2 gap-y-2"
            style={{
              gridTemplateColumns: "210px 36px 210px 36px 210px 36px 210px",
              gridTemplateAreas: `
                "c1 a1 c2 a2 c3 a3 c4"
                ".  .  .  .  lp .  ."
                ".  .  .  .  c5 .  ."
              `,
            }}
          >
            <div style={{ gridArea: "c1" }}>
              <NodeCard
                title={PIPELINE_NODES[0].title}
                summary={PIPELINE_NODES[0].summary}
                touches={PIPELINE_NODES[0].touches}
                example={<MemoryExample node={retrieveNode} />}
              />
            </div>
            <div style={{ gridArea: "a1" }} className="flex items-center justify-center">
              <HArrow />
            </div>
            <div style={{ gridArea: "c2" }}>
              <NodeCard
                title={PIPELINE_NODES[1].title}
                summary={PIPELINE_NODES[1].summary}
                touches={PIPELINE_NODES[1].touches}
                example={<DraftExample node={draftNode} />}
              />
            </div>
            <div style={{ gridArea: "a2" }} className="flex items-center justify-center">
              <HArrow />
            </div>
            <div style={{ gridArea: "c3" }}>
              <NodeCard
                title={PIPELINE_NODES[2].title}
                summary={PIPELINE_NODES[2].summary}
                touches={PIPELINE_NODES[2].touches}
                example={<VerifyExample nodes={verifyNodes} />}
              />
            </div>
            <div style={{ gridArea: "a3" }} className="flex items-center justify-center">
              <HArrow label="통과" tone="success" />
            </div>
            <div style={{ gridArea: "c4" }}>
              <NodeCard
                title={PIPELINE_NODES[3].title}
                summary={PIPELINE_NODES[3].summary}
                touches={PIPELINE_NODES[3].touches}
                example={<FinalizeExample node={finalizeNode} retryCount={selectedRun?.retry_count ?? null} />}
              />
            </div>
            <div style={{ gridArea: "lp" }} className="flex items-center justify-center gap-6 py-2">
              <VArrow direction="down" label="위반 발견" />
              <VArrow direction="up" label="재검증 (최대 2회)" />
            </div>
            <div style={{ gridArea: "c5" }}>
              <NodeCard
                title="⑤ 수정(재시도)"
                summary="이모지는 코드로 즉시 제거하고, 복붙은 겹친 예시를 빼고 좁게 재지시한다."
                touches="최대 2회까지 반복 — 그래도 안 풀리면 보류 상태로 ④최종화"
                variant="warning"
                example={examplesReady ? <FixExample nodes={fixNodes} /> : undefined}
              />
            </div>
          </div>
        </div>
        <p className="mt-4 text-[11px] text-muted">
          전체 실행은 LangSmith로 트레이싱되고, 재시도 루프가 끝나면 AI 초안과 사장님 최종본의 유사도가
          측정돼 아래 정확도 지표에 반영됩니다.
        </p>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-surface p-5">
        <h2 className="mb-4 text-sm font-semibold text-foreground">카테고리별 정확도 (AI초안 ↔ 사장님최종본 유사도)</h2>
        {accuracy === null ? (
          <p className="text-sm text-muted">불러오는 중...</p>
        ) : accuracy.length === 0 ? (
          <p className="text-sm text-muted">아직 측정된 데이터가 없습니다.</p>
        ) : (
          <div className="space-y-3">
            {accuracy.map((c) => (
              <div key={c.category}>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="text-foreground">{c.label}</span>
                  <span className="text-muted">
                    {(c.avg_similarity * 100).toFixed(1)}% · {c.sample_count}건
                  </span>
                </div>
                <div className="h-2 rounded-full bg-surface-2">
                  <div
                    className="h-2 rounded-full bg-accent"
                    style={{ width: `${Math.max(0, Math.min(100, c.avg_similarity * 100))}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-border-subtle bg-surface p-5">
        <h2 className="mb-1 text-sm font-semibold text-foreground">최근 실행 이력</h2>
        <p className="mb-4 text-xs text-muted">
          눌러서 열면 위 노드 구성도가 그 실행의 실제 데이터로 바뀌고, 아래에서 각 노드가 주고받은 원본 데이터도 볼 수 있어요.
        </p>
        {runs === null ? (
          <p className="text-sm text-muted">불러오는 중...</p>
        ) : runs.length === 0 ? (
          <p className="text-sm text-muted">
            표시할 실행 이력이 없어요 — LangSmith가 설정 안 됐거나(LANGSMITH_API_KEY), 아직 답글 생성이 없었을 수 있어요.
          </p>
        ) : (
          <div className="space-y-2">
            {runs.map((r) => (
              <div
                key={r.trace_id}
                className={`overflow-hidden rounded-xl border ${
                  selectedTraceId === r.trace_id ? "border-accent" : "border-border-subtle"
                }`}
              >
                <button
                  onClick={() => selectRun(r.trace_id)}
                  className="flex w-full items-center justify-between gap-3 bg-surface-2 px-4 py-3 text-left"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <StatusBadge passed={r.passed_verification} />
                    <span className="shrink-0 text-xs text-foreground">{r.category_label ?? "—"}</span>
                    <span className="truncate text-xs text-muted">{r.final_content_preview}</span>
                  </div>
                  <div className="flex shrink-0 items-center gap-3 text-[11px] text-muted">
                    {r.retry_count != null && r.retry_count > 0 && <span>재시도 {r.retry_count}회</span>}
                    <span>{r.started_at ? new Date(r.started_at).toLocaleString("ko-KR") : ""}</span>
                    <span>{openTraceId === r.trace_id ? "접기" : "자세히"}</span>
                  </div>
                </button>
                {openTraceId === r.trace_id && <RunDetailPanel trace_id={r.trace_id} />}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function HArrow({ label, tone = "muted" }: { label?: string; tone?: "muted" | "success" }) {
  const colorClass = tone === "success" ? "text-success" : "text-muted";
  return (
    <div className="flex flex-col items-center justify-center gap-1">
      {label && <span className={`text-[10px] font-medium ${colorClass}`}>{label}</span>}
      <svg viewBox="0 0 40 16" className={`h-4 w-8 ${colorClass}`} fill="none" aria-hidden="true">
        <line x1="1" y1="8" x2="30" y2="8" stroke="currentColor" strokeWidth="2" />
        <polygon points="30,2 39,8 30,14" fill="currentColor" />
      </svg>
    </div>
  );
}

function VArrow({ direction, label }: { direction: "down" | "up"; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <svg viewBox="0 0 16 40" className="h-10 w-4 shrink-0 text-warning" fill="none" aria-hidden="true">
        {direction === "down" ? (
          <>
            <line x1="8" y1="1" x2="8" y2="30" stroke="currentColor" strokeWidth="2" />
            <polygon points="2,30 8,39 14,30" fill="currentColor" />
          </>
        ) : (
          <>
            <line x1="8" y1="39" x2="8" y2="10" stroke="currentColor" strokeWidth="2" />
            <polygon points="2,10 8,1 14,10" fill="currentColor" />
          </>
        )}
      </svg>
      <span className="text-[11px] text-muted">{label}</span>
    </div>
  );
}
