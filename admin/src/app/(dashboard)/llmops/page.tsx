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
  total_tokens: number | null;
  total_cost: number | null;
  latency_ms: number | null;
};

type RunNode = {
  name: string;
  run_type: string;
  status: string;
  start_time: string | null;
  latency_ms: number | null;
  total_tokens: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_cost: number | null;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
};

type RunSummary = { total_tokens: number | null; total_cost: number | null; latency_ms: number | null; llm_call_count: number };

type RunDetail = { trace_id: string; summary: RunSummary; nodes: RunNode[] };

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

type NodeKey = "retrieve_memory" | "generate_draft" | "verify_draft" | "fix_draft" | "finalize";

const NODE_NAME_LABEL: Record<string, string> = {
  retrieve_memory: "기억 조회",
  generate_draft: "초안 생성",
  ChatAnthropic: "Claude Sonnet 호출",
  verify_draft: "검증",
  route_after_verify: "분기 판단",
  fix_draft: "수정(재시도)",
  finalize: "최종화",
};

const PIPELINE_NODES: { key: NodeKey; title: string; summary: string; touches: string }[] = [
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
    touches: "이모지 정규식 체크 · few-shot 예시와의 문자열 유사도(복붙 체크) · LLM 호출 없음",
  },
  {
    key: "finalize",
    title: "④ 최종화",
    summary: "검증을 통과했거나 재시도 상한(2회)에 닿으면 끝낸다.",
    touches: "최종 답글 텍스트 + 통과 여부(passed_verification) 반환",
  },
];

const FIX_NODE = {
  key: "fix_draft" as NodeKey,
  title: "⑤ 수정(재시도)",
  summary: "이모지는 코드로 즉시 제거하고, 복붙은 겹친 예시를 빼고 좁게 재지시한다.",
  touches: "최대 2회까지 반복 — 그래도 안 풀리면 보류 상태로 ④최종화",
};

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

function sumNumbers(values: (number | null)[]): number | null {
  const present = values.filter((v): v is number => typeof v === "number");
  if (present.length === 0) return null;
  return present.reduce((a, b) => a + b, 0);
}

function formatCost(v: number | null): string {
  return v == null ? "—" : `$${v.toFixed(4)}`;
}

function formatSeconds(ms: number | null): string {
  return ms == null ? "—" : `${(ms / 1000).toFixed(1)}초`;
}

function HighlightEmoji({ text }: { text: string }) {
  if (!text) return null;
  const parts = text.split(EMOJI_SPLIT);
  return (
    <>
      {parts.map((part, i) =>
        EMOJI_TEST.test(part) ? (
          // line-through은 이모지(컬러 글리프)에서 대부분 브라우저가 그려주지
          // 않아(실측 확인, 2026-10-08) 대신 배경 하이라이트로 표시한다.
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

function StatusBadge({ passed }: { passed: boolean | null }) {
  if (passed === null) return <span className="rounded bg-surface-2 px-2 py-0.5 text-[11px] text-muted">알 수 없음</span>;
  return passed ? (
    <span className="rounded bg-success/15 px-2 py-0.5 text-[11px] font-medium text-success">통과</span>
  ) : (
    <span className="rounded bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning">보류</span>
  );
}

function NodeTile({
  title,
  selected,
  onClick,
  badge,
  latencyMs,
  tokens,
  variant = "default",
}: {
  title: string;
  selected: boolean;
  onClick: () => void;
  badge: { label: string; tone: "success" | "warning" | "neutral" } | null;
  latencyMs: number | null;
  tokens: number | null;
  variant?: "default" | "warning";
}) {
  const toneClass =
    badge?.tone === "success" ? "bg-success/15 text-success" : badge?.tone === "warning" ? "bg-warning/15 text-warning" : "bg-surface text-muted";
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-full w-full rounded-xl border p-3 text-left transition ${
        selected
          ? "border-accent bg-accent-soft ring-2 ring-accent/50"
          : variant === "warning"
            ? "border-warning/40 bg-warning/5 hover:border-warning/70"
            : "border-border-subtle bg-surface-2 hover:border-accent/50"
      }`}
    >
      <p className={`text-sm font-semibold ${selected ? "text-accent" : variant === "warning" ? "text-warning" : "text-foreground"}`}>
        {title}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {badge && <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${toneClass}`}>{badge.label}</span>}
        {latencyMs != null && <span className="text-[10px] text-muted">{latencyMs}ms</span>}
        {tokens != null && <span className="text-[10px] text-muted">{tokens} tok</span>}
      </div>
      <p className="mt-1.5 text-[10px] text-accent">{selected ? "선택됨 — 아래에서 자세히" : "눌러서 자세히 보기 →"}</p>
    </button>
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

function LlmUsageLine({ nodes }: { nodes: RunNode[] }) {
  // LangSmith는 LLM 호출(ChatAnthropic)의 토큰/비용을 그 호출을 감싸는 부모
  // chain run(예: generate_draft)에도 그대로 롤업해서 올려준다(실측 확인) —
  // 그래서 run_type이 "llm"인 자식 노드를 따로 찾지 않고, 여기 넘어온 노드
  // 자신의 total_tokens가 채워져 있는지만 보면 된다. retrieve_memory/
  // verify_draft처럼 LLM을 아예 안 쓰는 노드는 total_tokens가 0으로 찍혀도
  // total_cost는 None으로 남는다(LangSmith가 실제로 그렇게 구분해서 줌).
  const tokens = sumNumbers(nodes.map((n) => n.total_tokens));
  const cost = sumNumbers(nodes.map((n) => n.total_cost));
  if (cost == null) {
    return <p className="text-[11px] text-muted">이 단계는 LLM을 호출하지 않아요(결정론적 코드 처리).</p>;
  }
  return (
    <p className="text-[11px] text-accent">
      ⚡ 토큰 {tokens ?? "—"} · {formatCost(cost)}
    </p>
  );
}

function MemoryExample({ node }: { node: RunNode | undefined }) {
  if (!node) return <p className="mt-3 border-t border-border-subtle pt-3 text-xs text-muted">이 실행엔 데이터가 없어요.</p>;
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
            {review.is_sensitive && <span className="rounded bg-danger/15 px-1.5 py-0.5 text-[10px] text-danger">민감 리뷰</span>}
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
  if (!node) return <p className="mt-3 border-t border-border-subtle pt-3 text-xs text-muted">이 실행엔 데이터가 없어요.</p>;
  const draft = asString(node.outputs.draft);
  return (
    <ExampleSection label="실제 예시">
      <LlmUsageLine nodes={[node]} />
      <p className="rounded-lg bg-surface px-2.5 py-2 text-xs text-foreground">{draft}</p>
    </ExampleSection>
  );
}

function VerifyExample({ nodes }: { nodes: RunNode[] }) {
  if (nodes.length === 0) return <p className="mt-3 border-t border-border-subtle pt-3 text-xs text-muted">이 실행엔 데이터가 없어요.</p>;
  return (
    <ExampleSection label="검증 시도">
      <LlmUsageLine nodes={nodes} />
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
      <LlmUsageLine nodes={nodes} />
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
  if (!node) return <p className="mt-3 border-t border-border-subtle pt-3 text-xs text-muted">이 실행엔 데이터가 없어요.</p>;
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

function RunRawDetailPanel({ trace_id }: { trace_id: string }) {
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
            <p className="text-[11px] text-muted">
              {node.latency_ms != null ? `${node.latency_ms}ms` : ""}
              {node.total_tokens != null ? ` · ${node.total_tokens} tok` : ""}
              {node.total_cost != null ? ` · ${formatCost(node.total_cost)}` : ""}
            </p>
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
  const [selectedNodeKey, setSelectedNodeKey] = useState<NodeKey | null>(null);

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
  const examplesReady = detail !== null;

  useEffect(() => {
    if (examplesReady && selectedNodeKey === null) setSelectedNodeKey("retrieve_memory");
  }, [examplesReady, selectedNodeKey]);

  const nodesNamed = (name: string) => (detail?.nodes.filter((n) => n.name === name) ?? []);
  const retrieveNodes = examplesReady ? nodesNamed("retrieve_memory") : [];
  const draftNodes = examplesReady ? nodesNamed("generate_draft") : [];
  const verifyNodes = examplesReady ? nodesNamed("verify_draft") : [];
  const fixNodes = examplesReady ? nodesNamed("fix_draft") : [];
  const finalizeNodes = examplesReady ? nodesNamed("finalize") : [];

  const selectRun = (trace_id: string) => {
    setSelectedTraceId(trace_id);
    setOpenTraceId((prev) => (prev === trace_id ? prev : trace_id));
  };

  const tileFor = (key: NodeKey) => {
    switch (key) {
      case "retrieve_memory":
        return { nodes: retrieveNodes, badge: retrieveNodes.length > 0 ? { label: "정상", tone: "success" as const } : null };
      case "generate_draft":
        return { nodes: draftNodes, badge: draftNodes.length > 0 ? { label: "정상", tone: "success" as const } : null };
      case "verify_draft":
        return {
          nodes: verifyNodes,
          badge:
            verifyNodes.length === 0
              ? null
              : selectedRun?.passed_verification
                ? { label: "통과", tone: "success" as const }
                : { label: "보류", tone: "warning" as const },
        };
      case "finalize":
        return {
          nodes: finalizeNodes,
          badge:
            finalizeNodes.length === 0
              ? null
              : selectedRun?.passed_verification
                ? { label: "통과", tone: "success" as const }
                : { label: "보류", tone: "warning" as const },
        };
      case "fix_draft":
        return {
          nodes: fixNodes,
          badge: !examplesReady ? null : fixNodes.length > 0 ? { label: `재시도 ${fixNodes.length}회`, tone: "warning" as const } : { label: "재시도 없음", tone: "neutral" as const },
        };
    }
  };

  const renderDetailExample = (key: NodeKey) => {
    switch (key) {
      case "retrieve_memory":
        return <MemoryExample node={retrieveNodes[0]} />;
      case "generate_draft":
        return <DraftExample node={draftNodes[0]} />;
      case "verify_draft":
        return <VerifyExample nodes={verifyNodes} />;
      case "fix_draft":
        return <FixExample nodes={fixNodes} />;
      case "finalize":
        return <FinalizeExample node={finalizeNodes[0]} retryCount={selectedRun?.retry_count ?? null} />;
    }
  };

  const allNodeMeta = [...PIPELINE_NODES, FIX_NODE];
  const selectedMeta = selectedNodeKey ? allNodeMeta.find((n) => n.key === selectedNodeKey) : null;

  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h1 className="text-lg font-semibold">LLMOps — 답글 생성 파이프라인</h1>
        <p className="text-xs text-muted">
          리뷰 답글이 실제로 어떤 노드를 거쳐, 어떤 데이터를 참조해서 만들어지는지 추적합니다. 노드를 클릭하면 아래에 자세한 내용이 나와요.
        </p>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-surface p-5">
        <h2 className="mb-1 text-sm font-semibold text-foreground">노드 구성도</h2>
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
          <>
            <p className="mb-2 text-[11px] text-accent">
              표시 중인 예시: {selectedRun?.category_label ?? "—"} ·{" "}
              {selectedRun?.started_at ? new Date(selectedRun.started_at).toLocaleString("ko-KR") : ""}
              {" — 아래 "}
              <span className="text-muted">최근 실행 이력</span>에서 다른 사례를 고를 수 있어요.
            </p>
            <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 rounded-xl border border-border-subtle bg-surface-2/60 px-4 py-2.5 text-xs">
              <span className="text-muted">
                모델 호출 <span className="font-medium text-foreground">{detail.summary.llm_call_count}회</span>
              </span>
              <span className="text-muted">
                토큰 <span className="font-medium text-foreground">{detail.summary.total_tokens ?? "—"}</span>
              </span>
              <span className="text-muted">
                비용 <span className="font-medium text-foreground">{formatCost(detail.summary.total_cost)}</span>
              </span>
              <span className="text-muted">
                총 소요시간 <span className="font-medium text-foreground">{formatSeconds(detail.summary.latency_ms)}</span>
              </span>
            </div>
          </>
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
            {PIPELINE_NODES.map((n, i) => {
              const { nodes, badge } = tileFor(n.key);
              return (
                <div key={n.key} style={{ gridArea: `c${i + 1}` }}>
                  <NodeTile
                    title={n.title}
                    selected={selectedNodeKey === n.key}
                    onClick={() => setSelectedNodeKey(n.key)}
                    badge={badge}
                    latencyMs={sumNumbers(nodes.map((x) => x.latency_ms))}
                    tokens={sumNumbers(nodes.map((x) => x.total_tokens))}
                  />
                </div>
              );
            })}
            <div style={{ gridArea: "a1" }} className="flex items-center justify-center">
              <HArrow />
            </div>
            <div style={{ gridArea: "a2" }} className="flex items-center justify-center">
              <HArrow />
            </div>
            <div style={{ gridArea: "a3" }} className="flex items-center justify-center">
              <HArrow label="통과" tone="success" />
            </div>
            <div style={{ gridArea: "lp" }} className="flex items-center justify-center gap-6 py-2">
              <VArrow direction="down" label="위반 발견" />
              <VArrow direction="up" label="재검증 (최대 2회)" />
            </div>
            <div style={{ gridArea: "c5" }}>
              <NodeTile
                title={FIX_NODE.title}
                selected={selectedNodeKey === "fix_draft"}
                onClick={() => setSelectedNodeKey("fix_draft")}
                badge={tileFor("fix_draft").badge}
                latencyMs={sumNumbers(fixNodes.map((x) => x.latency_ms))}
                tokens={sumNumbers(fixNodes.map((x) => x.total_tokens))}
                variant="warning"
              />
            </div>
          </div>
        </div>

        {selectedMeta && (
          <div className="mt-5 rounded-xl border border-accent/40 bg-accent-soft/40 p-4">
            <p className="text-sm font-semibold text-accent">{selectedMeta.title}</p>
            <p className="mt-1 text-xs text-foreground">{selectedMeta.summary}</p>
            <p className="mt-1 text-[11px] text-muted">{selectedMeta.touches}</p>
            {renderDetailExample(selectedMeta.key)}
          </div>
        )}

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
                    {r.total_tokens != null && <span>{r.total_tokens} tok</span>}
                    {r.total_cost != null && <span>{formatCost(r.total_cost)}</span>}
                    {r.retry_count != null && r.retry_count > 0 && <span>재시도 {r.retry_count}회</span>}
                    <span>{r.started_at ? new Date(r.started_at).toLocaleString("ko-KR") : ""}</span>
                    <span>{openTraceId === r.trace_id ? "접기" : "원본 데이터"}</span>
                  </div>
                </button>
                {openTraceId === r.trace_id && <RunRawDetailPanel trace_id={r.trace_id} />}
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
