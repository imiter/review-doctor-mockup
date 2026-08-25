"""Voyage AI 임베딩 API 얇은 래퍼 — golden_examples 의미 기반 검색에 쓴다.
Anthropic은 임베딩을 API로 제공하지 않아 별도 벤더가 필요했다(VOYAGE_API_KEY
환경변수, voyageai.com에서 발급 — 계정당 2억 토큰까지 무료라 이 프로젝트
규모(골든 예시 수백 건)에서는 사실상 비용이 들지 않는다). document(저장)/
query(검색) 두 함수만 통해 호출해 테스트에서 monkeypatch로 갈음한다.

REST 직접 호출을 쓴다 — 이미 requirements.txt에 있는 httpx로 충분한 단순한
POST 하나라, 별도 SDK(voyageai 패키지)를 추가하지 않았다."""

import os

import httpx

EMBEDDING_MODEL = "voyage-4"
EMBEDDING_DIM = 1024
_API_URL = "https://api.voyageai.com/v1/embeddings"


def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    resp = httpx.post(
        _API_URL,
        headers={"Authorization": f"Bearer {os.environ['VOYAGE_API_KEY']}"},
        json={
            "input": texts, "model": EMBEDDING_MODEL,
            "input_type": input_type, "output_dimension": EMBEDDING_DIM,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]


def embed_document(text: str) -> list[float]:
    """골든 예시를 저장할 때 review_text를 벡터화한다."""
    return _embed([text], "document")[0]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """여러 건을 한 번의 API 호출로 벡터화한다(백필용) — 개별 호출보다
    요청 수가 훨씬 적다. 호출부가 배치 크기를 적절히 나눠서 넘겨야 한다
    (Voyage 요청 하나당 토큰/건수 한도가 있다)."""
    return _embed(texts, "document")


def embed_query(text: str) -> list[float]:
    """검색 시점에 새 리뷰 내용을 벡터화한다. document와 프롬프트가 달라
    (Voyage가 검색/저장 각각에 다른 프롬프트를 앞에 붙인다) 반드시 구분해
    써야 한다."""
    return _embed([text], "query")[0]
