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
    # timeout=60.0 — SDK 기본(수 분 + 재시도)을 쓰면 API가 저하됐을 때
    # "답글 생성" 버튼 클릭 하나가 수십 분씩 걸릴 수 있다.
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=60.0)


def _first_text(content_blocks: list) -> str:
    """응답의 첫 텍스트 블록을 찾아 반환한다. Sonnet은 기본으로 adaptive
    thinking이 켜져 있어 content[0]이 ThinkingBlock(.text 속성 없음)일 수
    있으므로, content[0]을 그냥 인덱싱하면 안 되고 첫 text 블록을 스캔해야
    한다."""
    for block in content_blocks:
        if block.type == "text":
            return block.text
    raise RuntimeError("Anthropic 응답에 텍스트 블록이 없습니다")


def call_haiku(system: str, user: str, *, max_tokens: int = 300) -> str:
    response = _client().messages.create(
        model=HAIKU_MODEL, max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return _first_text(response.content)


def call_sonnet(system: str, user: str, *, max_tokens: int = 1000) -> str:
    response = _client().messages.create(
        model=SONNET_MODEL, max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
        thinking={"type": "disabled"},
    )
    return _first_text(response.content)
