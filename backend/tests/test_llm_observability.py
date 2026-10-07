from datetime import datetime, timedelta, timezone

from app.llm import observability


class _FakeRun:
    def __init__(
        self, id, name, run_type, status, start_time, end_time=None, inputs=None, outputs=None,
        total_tokens=None, prompt_tokens=None, completion_tokens=None, total_cost=None,
    ):
        self.id = id
        self.name = name
        self.run_type = run_type
        self.status = status
        self.start_time = start_time
        self.end_time = end_time
        self.inputs = inputs
        self.outputs = outputs
        self.total_tokens = total_tokens
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_cost = total_cost


def test_list_recent_runs_returns_empty_without_langsmith_key(monkeypatch):
    """_no_langsmith_key autouse fixture가 키를 지워두므로, 몽키패치 없이도
    바로 빈 리스트가 나와야 한다(관측 기능 미설정 시 조용히 스킵)."""
    assert observability.list_recent_runs() == []


def test_list_recent_runs_shapes_root_runs(monkeypatch):
    now = datetime.now(timezone.utc)
    older = now - timedelta(minutes=5)

    class _FakeClient:
        def list_runs(self, **kwargs):
            assert kwargs["is_root"] is True
            return [
                _FakeRun("id-1", "LangGraph", "chain", "success", older, older + timedelta(seconds=9),
                         outputs={"category_label": "배달(지연/파손)", "passed_verification": True, "retry_count": 0, "final_content": "안녕하세요 " * 20},
                         total_tokens=500, total_cost=0.002),
                _FakeRun("id-2", "LangGraph", "chain", "success", now, now + timedelta(seconds=3),
                         outputs={"category_label": "맛", "passed_verification": False, "retry_count": 2, "final_content": "죄송"},
                         total_tokens=1200, total_cost=0.0071),
            ]

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FakeClient)

    rows = observability.list_recent_runs(limit=10)

    assert len(rows) == 2
    assert rows[0]["trace_id"] == "id-2"  # 최신순 정렬
    assert rows[0]["passed_verification"] is False
    assert rows[0]["retry_count"] == 2
    assert rows[0]["total_tokens"] == 1200
    assert rows[0]["total_cost"] == 0.0071
    assert rows[0]["latency_ms"] == 3000
    assert rows[1]["trace_id"] == "id-1"
    assert len(rows[1]["final_content_preview"]) <= 80


def test_list_recent_runs_handles_missing_token_cost_gracefully(monkeypatch):
    """비-LLM run이거나 LangSmith가 아직 비용을 계산 못 한 경우 total_cost가
    None일 수 있다 — 0으로 둔갑시키지 않고 그대로 None을 돌려줘야 프론트가
    "측정 안 됨"과 "0원"을 구분할 수 있다."""
    now = datetime.now(timezone.utc)

    class _FakeClient:
        def list_runs(self, **kwargs):
            return [_FakeRun("id-1", "LangGraph", "chain", "success", now, outputs={})]

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FakeClient)

    rows = observability.list_recent_runs()

    assert rows[0]["total_tokens"] is None
    assert rows[0]["total_cost"] is None
    assert rows[0]["latency_ms"] is None  # end_time 없음


def test_list_recent_runs_returns_empty_when_langsmith_call_fails(monkeypatch):
    class _FailingClient:
        def list_runs(self, **kwargs):
            raise RuntimeError("LangSmith API 실패")

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FailingClient)

    assert observability.list_recent_runs() == []


def test_get_run_detail_returns_none_without_langsmith_key():
    assert observability.get_run_detail("some-trace-id") is None


def test_get_run_detail_excludes_wrapper_run_from_nodes_but_keeps_its_totals_as_summary(monkeypatch):
    now = datetime.now(timezone.utc)
    later = now + timedelta(milliseconds=500)
    root_end = now + timedelta(seconds=9)

    class _FakeClient:
        def list_runs(self, **kwargs):
            assert kwargs["trace_id"] == "trace-abc"
            return [
                _FakeRun("root", "LangGraph", "chain", "success", now, root_end,
                         inputs={"db": "<Session>"}, outputs={"final_content": "답글"},
                         total_tokens=896, total_cost=0.005344),
                _FakeRun("n1", "retrieve_memory", "chain", "success", now, later,
                         inputs={"db": "<Session>", "review": "<Review>"},
                         outputs={"style_rules": "규칙", "examples_preview": [{"review_text": "리뷰"}]}),
                _FakeRun("n2", "ChatAnthropic", "llm", "success", now, later,
                         inputs={}, outputs={},
                         total_tokens=896, prompt_tokens=452, completion_tokens=444, total_cost=0.005344),
            ]

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FakeClient)

    detail = observability.get_run_detail("trace-abc")

    assert detail["trace_id"] == "trace-abc"
    assert len(detail["nodes"]) == 2  # LangGraph wrapper는 nodes에서 빠진다
    node = detail["nodes"][0]
    assert node["name"] == "retrieve_memory"
    assert "db" not in node["inputs"]
    assert "review" not in node["inputs"]
    assert node["outputs"]["style_rules"] == "규칙"
    assert node["latency_ms"] == 500
    assert node["total_tokens"] is None  # chain 노드는 토큰이 안 찍힘(이 테스트에서는)

    llm_node = detail["nodes"][1]
    assert llm_node["name"] == "ChatAnthropic"
    assert llm_node["total_tokens"] == 896
    assert llm_node["prompt_tokens"] == 452
    assert llm_node["completion_tokens"] == 444
    assert llm_node["total_cost"] == 0.005344

    # 요약(summary)은 LangGraph 루트 run의 전체 합산값을 그대로 쓴다.
    assert detail["summary"]["total_tokens"] == 896
    assert detail["summary"]["total_cost"] == 0.005344
    assert detail["summary"]["latency_ms"] == 9000
    assert detail["summary"]["llm_call_count"] == 1


def test_get_run_detail_returns_none_when_no_runs_found(monkeypatch):
    class _FakeClient:
        def list_runs(self, **kwargs):
            return []

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FakeClient)

    assert observability.get_run_detail("nonexistent") is None
