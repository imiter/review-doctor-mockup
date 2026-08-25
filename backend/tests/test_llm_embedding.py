import pytest

from app.llm.embedding import embed_document, embed_documents, embed_query


class _FakeResponse:
    def __init__(self, embeddings):
        self._embeddings = embeddings

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": [{"embedding": e, "index": i} for i, e in enumerate(self._embeddings)]}


def test_embed_document_sends_document_input_type(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    captured = {}

    def _fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse([[0.1, 0.2, 0.3]])

    monkeypatch.setattr("httpx.post", _fake_post)

    result = embed_document("리뷰 내용")

    assert result == [0.1, 0.2, 0.3]
    assert captured["json"]["input_type"] == "document"
    assert captured["json"]["input"] == ["리뷰 내용"]
    assert captured["headers"]["Authorization"] == "Bearer test-key"


def test_embed_query_sends_query_input_type(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    captured = {}

    def _fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeResponse([[0.4, 0.5]])

    monkeypatch.setattr("httpx.post", _fake_post)

    result = embed_query("새 리뷰 내용")

    assert result == [0.4, 0.5]
    assert captured["json"]["input_type"] == "query"


def test_embed_documents_batches_multiple_texts(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

    def _fake_post(url, headers, json, timeout):
        return _FakeResponse([[1.0], [2.0], [3.0]])

    monkeypatch.setattr("httpx.post", _fake_post)

    result = embed_documents(["a", "b", "c"])

    assert result == [[1.0], [2.0], [3.0]]


def test_embed_document_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(KeyError):
        embed_document("리뷰 내용")
