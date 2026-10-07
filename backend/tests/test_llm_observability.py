from datetime import datetime, timedelta, timezone

from app.llm import observability


class _FakeRun:
    def __init__(self, id, name, run_type, status, start_time, end_time=None, inputs=None, outputs=None):
        self.id = id
        self.name = name
        self.run_type = run_type
        self.status = status
        self.start_time = start_time
        self.end_time = end_time
        self.inputs = inputs
        self.outputs = outputs


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
                _FakeRun("id-1", "LangGraph", "chain", "success", older,
                         outputs={"category_label": "배달(지연/파손)", "passed_verification": True, "retry_count": 0, "final_content": "안녕하세요 " * 20}),
                _FakeRun("id-2", "LangGraph", "chain", "success", now,
                         outputs={"category_label": "맛", "passed_verification": False, "retry_count": 2, "final_content": "죄송"}),
            ]

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FakeClient)

    rows = observability.list_recent_runs(limit=10)

    assert len(rows) == 2
    assert rows[0]["trace_id"] == "id-2"  # 최신순 정렬
    assert rows[0]["passed_verification"] is False
    assert rows[0]["retry_count"] == 2
    assert rows[1]["trace_id"] == "id-1"
    assert len(rows[1]["final_content_preview"]) <= 80


def test_list_recent_runs_returns_empty_when_langsmith_call_fails(monkeypatch):
    class _FailingClient:
        def list_runs(self, **kwargs):
            raise RuntimeError("LangSmith API 실패")

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FailingClient)

    assert observability.list_recent_runs() == []


def test_get_run_detail_returns_none_without_langsmith_key():
    assert observability.get_run_detail("some-trace-id") is None


def test_get_run_detail_excludes_wrapper_run_and_opaque_keys(monkeypatch):
    now = datetime.now(timezone.utc)
    later = now + timedelta(milliseconds=500)

    class _FakeClient:
        def list_runs(self, **kwargs):
            assert kwargs["trace_id"] == "trace-abc"
            return [
                _FakeRun("root", "LangGraph", "chain", "success", now,
                         inputs={"db": "<Session>"}, outputs={"final_content": "답글"}),
                _FakeRun("n1", "retrieve_memory", "chain", "success", now, later,
                         inputs={"db": "<Session>", "review": "<Review>"},
                         outputs={"style_rules": "규칙", "examples_preview": [{"review_text": "리뷰"}]}),
            ]

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FakeClient)

    detail = observability.get_run_detail("trace-abc")

    assert detail["trace_id"] == "trace-abc"
    assert len(detail["nodes"]) == 1  # LangGraph wrapper 제외
    node = detail["nodes"][0]
    assert node["name"] == "retrieve_memory"
    assert "db" not in node["inputs"]
    assert "review" not in node["inputs"]
    assert node["outputs"]["style_rules"] == "규칙"
    assert node["latency_ms"] == 500


def test_get_run_detail_returns_none_when_no_runs_found(monkeypatch):
    class _FakeClient:
        def list_runs(self, **kwargs):
            return []

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(observability, "_LangSmithClient", _FakeClient)

    assert observability.get_run_detail("nonexistent") is None
