"""관리자 LLMOps 대시보드용 LangSmith 조회 — 실제 답글 생성 트레이스를
가져와 노드별 입출력을 사람이 읽을 수 있는 형태로 돌려준다. 실측 확인
(2026-10-07): LangGraph가 자동으로 노드마다 자식 run을 만들고
(retrieve_memory/generate_draft/ChatAnthropic/verify_draft/
route_after_verify/[fix_draft]/finalize), 각 run의 inputs/outputs에
AgentState 딕셔너리가 그대로 찍힌다 — db/review/store/style은 SQLAlchemy
객체라 object repr로만 찍혀 의미가 없어 응답에서 뺀다(그 외 rules,
style_rules, draft, violations, examples_preview 등은 전부 유용한 값
그대로 찍힌다).

LANGSMITH_API_KEY가 없으면(로컬 개발 등) 빈 결과로 조용히 폴백한다 —
이 관측 기능이 안 된다고 핵심 기능(답글 생성)에 영향을 주면 안 된다는
이 프로젝트의 다른 LLM 폴백들과 같은 원칙.

토큰/비용(2026-10-08 추가): LangSmith를 쓰는 핵심 이유 중 하나가 토큰/비용
추적인데 기존 구현엔 전혀 없었다 — LangSmith Run 객체는 LLM 호출
(ChatAnthropic)의 토큰/비용을 자동으로 계산해 그 run 자신뿐 아니라 부모
chain run(generate_draft)과 최상위 루트 run(LangGraph)까지 전부 롤업해서
올려준다는 걸 실측 확인했다 — 그래서 별도 집계 코드 없이 루트 run의
total_tokens/total_cost를 "이 실행 전체"의 요약으로 그대로 쓴다."""

import os
from datetime import datetime, timezone

try:
    from langsmith import Client as _LangSmithClient
except ImportError:  # pragma: no cover — langsmith는 requirements.txt에 있어 항상 설치돼 있다
    _LangSmithClient = None

# 트레이스에 찍히지만 사람이 읽기엔 의미 없는 키 — 전부 SQLAlchemy 객체라
# "<app.models.Review object at 0x...>" 같은 repr로만 찍힌다.
_OPAQUE_KEYS = {"db", "review", "store", "style"}

# "LangGraph"는 전체 실행을 감싸는 최상위 run이지 노드 자체가 아니라
# 상세 화면에서는 뺀다(list_recent_runs의 대상은 바로 이 run이다 —
# is_root=True로 조회).
_WRAPPER_RUN_NAME = "LangGraph"


def _client():
    if _LangSmithClient is None or not os.environ.get("LANGSMITH_API_KEY"):
        return None
    return _LangSmithClient()


def _project_name() -> str:
    return os.environ.get("LANGSMITH_PROJECT", "default")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _summarize(data: dict | None) -> dict:
    if not data:
        return {}
    return {k: v for k, v in data.items() if k not in _OPAQUE_KEYS}


def list_recent_runs(limit: int = 20) -> list[dict]:
    """최근 run_agent 호출(최상위 트레이스) 목록. 호출마다 LangSmith에
    기록된 실제 결과(카테고리, 통과 여부, 재시도 횟수, 최종 답글 일부)를
    보여준다."""
    client = _client()
    if client is None:
        return []
    try:
        runs = list(client.list_runs(project_name=_project_name(), is_root=True, limit=limit))
    except Exception:
        return []

    runs.sort(key=lambda r: r.start_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    rows = []
    for r in runs:
        outputs = r.outputs or {}
        latency_ms = None
        if r.start_time is not None and r.end_time is not None:
            latency_ms = int((r.end_time - r.start_time).total_seconds() * 1000)
        rows.append({
            "trace_id": str(r.id),
            "started_at": _iso(r.start_time),
            "status": r.status,
            "category_label": outputs.get("category_label"),
            "passed_verification": outputs.get("passed_verification"),
            "retry_count": outputs.get("retry_count"),
            "final_content_preview": (outputs.get("final_content") or "")[:80],
            # LangGraph 루트 run은 그 안의 모든 LLM 호출(ChatAnthropic)의 토큰/비용이
            # 자동으로 합산돼 올라온다(LangSmith가 부모 run에 롤업) — 사장님이 LangSmith를
            # 쓰는 이유 자체가 토큰/비용 추적이라, 실행 목록에서부터 바로 보여준다.
            "total_tokens": r.total_tokens,
            "total_cost": float(r.total_cost) if r.total_cost is not None else None,
            "latency_ms": latency_ms,
        })
    return rows


def get_run_detail(trace_id: str) -> dict | None:
    """trace_id 하나의 전체 노드별 입출력. 노드를 실행 순서대로 정렬해서
    돌려준다 — "어느 노드가 어떤 데이터를 받아서 뭘 만들어냈는지"를 그대로
    보여주는 게 목적이라, 원본 AgentState 키들을 거의 그대로 노출한다
    (db/review/store/style만 뺀다)."""
    client = _client()
    if client is None:
        return None
    try:
        children = list(client.list_runs(project_name=_project_name(), trace_id=trace_id))
    except Exception:
        return None
    if not children:
        return None

    children.sort(key=lambda r: r.start_time or datetime.min.replace(tzinfo=timezone.utc))

    nodes = []
    summary = {"total_tokens": None, "total_cost": None, "latency_ms": None, "llm_call_count": 0}
    for r in children:
        latency_ms = None
        if r.start_time is not None and r.end_time is not None:
            latency_ms = int((r.end_time - r.start_time).total_seconds() * 1000)

        if r.name == _WRAPPER_RUN_NAME:
            # 노드 목록엔 안 넣지만(그래프 자체를 감싸는 run이라 "노드"가
            # 아니다), 토큰/비용/전체 소요시간은 여기(루트 run)에 전체 합산
            # 값이 이미 올라와 있어 그대로 요약으로 쓴다.
            summary["total_tokens"] = r.total_tokens
            summary["total_cost"] = float(r.total_cost) if r.total_cost is not None else None
            summary["latency_ms"] = latency_ms
            continue

        if r.run_type == "llm":
            summary["llm_call_count"] += 1

        nodes.append({
            "name": r.name,
            "run_type": r.run_type,
            "status": r.status,
            "start_time": _iso(r.start_time),
            "latency_ms": latency_ms,
            "total_tokens": r.total_tokens,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "total_cost": float(r.total_cost) if r.total_cost is not None else None,
            "inputs": _summarize(r.inputs),
            "outputs": _summarize(r.outputs),
        })
    return {"trace_id": trace_id, "summary": summary, "nodes": nodes}
