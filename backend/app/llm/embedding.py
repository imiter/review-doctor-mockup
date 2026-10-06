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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """두 임베딩 벡터의 코사인 유사도(-1~1). golden_examples 검색이 쓰는
    pgvector의 <-> 연산자(DB 레벨, 거리)와 달리, 이건 두 벡터를 이미
    메모리에 들고 있을 때 쓰는 순수 Python 계산이다(app/llm/feedback.py가
    AI 초안과 사장님 최종본의 유사도를 잴 때 쓴다, 스펙 4.2절).
    둘 중 하나가 영벡터면(이론상으로만 가능, 실제 텍스트 임베딩에서는
    안 나옴) 0으로 돌려준다 — ZeroDivisionError를 내는 대신."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
