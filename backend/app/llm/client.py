"""Anthropic API 얇은 래퍼 — 이 프로젝트에서 처음 도입하는 실제 외부 AI
호출이다(Claude Pro 구독과는 별도로 과금되는 API 키가 필요하다 —
ANTHROPIC_API_KEY 환경변수, console.anthropic.com에서 발급). 분류/생성/
스타일 추출 각 모듈은 이 파일의 두 함수만 통해 Anthropic API를 호출한다
— 테스트에서 이 두 함수만 monkeypatch하면 실제 API 호출 없이 전부
검증할 수 있다."""

import os

import anthropic

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-5"


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def call_haiku(system: str, user: str, *, max_tokens: int = 300) -> str:
    response = _client().messages.create(
        model=HAIKU_MODEL, max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


def call_sonnet(system: str, user: str, *, max_tokens: int = 1000) -> str:
    response = _client().messages.create(
        model=SONNET_MODEL, max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text
