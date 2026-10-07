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

function NodeCard({
  title,
  summary,
  touches,
  variant = "default",
}: {
  title: string;
  summary: string;
  touches: string;
  variant?: "default" | "warning";
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

function RunDetailPanel({ trace_id }: { trace_id: string }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    apiGet<RunDetail>(`/admin/llmops/runs/${trace_id}`)
      .then(setDetail)
      .catch(() => setError("LangSmith에서 이 트레이스를 찾을 수 없어요(보관 기간이 지났거나 설정 문제일 수 있어요)."));
  }, [trace_id]);

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

  useEffect(() => {
    apiGet<{ runs: RunRow[] }>("/admin/llmops/runs?limit=20").then((r) => setRuns(r.runs));
    apiGet<{ categories: AccuracyCategory[] }>("/admin/llmops/accuracy").then((r) => setAccuracy(r.categories));
  }, []);

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
        <p className="mb-4 text-[11px] text-muted">
          리뷰 한 건이 답글로 나오기까지 거치는 노드와, 노드 사이에 오가는 데이터입니다.
        </p>
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
              <NodeCard title={PIPELINE_NODES[0].title} summary={PIPELINE_NODES[0].summary} touches={PIPELINE_NODES[0].touches} />
            </div>
            <div style={{ gridArea: "a1" }} className="flex items-center justify-center">
              <HArrow />
            </div>
            <div style={{ gridArea: "c2" }}>
              <NodeCard title={PIPELINE_NODES[1].title} summary={PIPELINE_NODES[1].summary} touches={PIPELINE_NODES[1].touches} />
            </div>
            <div style={{ gridArea: "a2" }} className="flex items-center justify-center">
              <HArrow />
            </div>
            <div style={{ gridArea: "c3" }}>
              <NodeCard title={PIPELINE_NODES[2].title} summary={PIPELINE_NODES[2].summary} touches={PIPELINE_NODES[2].touches} />
            </div>
            <div style={{ gridArea: "a3" }} className="flex items-center justify-center">
              <HArrow label="통과" tone="success" />
            </div>
            <div style={{ gridArea: "c4" }}>
              <NodeCard title={PIPELINE_NODES[3].title} summary={PIPELINE_NODES[3].summary} touches={PIPELINE_NODES[3].touches} />
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
        <p className="mb-4 text-xs text-muted">눌러서 열면 그 실행이 각 노드에서 실제로 주고받은 데이터를 볼 수 있어요.</p>
        {runs === null ? (
          <p className="text-sm text-muted">불러오는 중...</p>
        ) : runs.length === 0 ? (
          <p className="text-sm text-muted">
            표시할 실행 이력이 없어요 — LangSmith가 설정 안 됐거나(LANGSMITH_API_KEY), 아직 답글 생성이 없었을 수 있어요.
          </p>
        ) : (
          <div className="space-y-2">
            {runs.map((r) => (
              <div key={r.trace_id} className="overflow-hidden rounded-xl border border-border-subtle">
                <button
                  onClick={() => setOpenTraceId(openTraceId === r.trace_id ? null : r.trace_id)}
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
