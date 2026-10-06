"""LangGraph 에이전트 루프 전용 LLM 호출 래퍼. backend/app/llm/client.py의
call_sonnet과 똑같은 "system+user 문자열 → 텍스트" 모양을 유지해 테스트에서
이 함수 하나만 monkeypatch하면 된다(기존 client.call_sonnet 테스트 패턴과
동일). classify.py/onboarding.py의 Haiku 호출은 범위 밖이라 client.py의
raw Anthropic SDK 경로를 그대로 쓴다 — 이 파일은 에이전트 루프(Sonnet
답글 생성)에만 쓴다.

ChatAnthropic(langchain-anthropic 1.7.5) 생성자 키워드는 pydantic 필드의
별칭(alias)이다 — 실제 필드명은 각각 model_name/anthropic_api_key/
max_tokens/default_request_timeout이지만, 여기 쓴 model/api_key/
max_tokens/timeout 별칭으로도 그대로 생성된다(설치된 버전에서 직접 확인).

.invoke() 응답(AIMessage)의 .content는 버전에 따라 모양이 다르다 —
langchain_anthropic.chat_models.BaseChatAnthropic._format_output 소스를
직접 확인한 결과, 응답에 텍스트 블록이 정확히 하나뿐이고(citations 없음)
tool_use가 없으면 content가 바로 문자열이 되지만, 그 외의 경우(블록이
여럿이거나 citations가 붙은 경우 등)는 content가 원본 블록 dict 리스트로
남는다(각 블록은 dict라 ["text"]로 접근, .text 속성이 아니다). thinking을
비활성화하고 tool을 안 쓰는 이 함수의 호출 방식상 거의 항상 문자열
케이스를 타지만, 방어적으로 두 모양 다 처리한다."""

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

SONNET_MODEL = "claude-sonnet-5"


def _client(max_tokens: int) -> ChatAnthropic:
    # timeout=60.0 — client.py의 call_sonnet과 동일한 이유(API 저하 시
    # 요청 하나가 수십 분 걸리는 것을 막음).
    return ChatAnthropic(
        model=SONNET_MODEL, api_key=os.environ["ANTHROPIC_API_KEY"],
        max_tokens=max_tokens, timeout=60.0,
        thinking={"type": "disabled"},
    )


def _text_content(content) -> str:
    """response.content가 문자열이면 그대로, 블록 dict 리스트면 첫 text
    블록의 "text"를 반환한다 (client.py의 _first_text와 동일한 목적,
    다만 dict 리스트를 받는다는 점만 다르다)."""
    if isinstance(content, str):
        return content
    for block in content:
        if block.get("type") == "text":
            return block["text"]
    raise RuntimeError("ChatAnthropic 응답에 텍스트 블록이 없습니다")


def call_sonnet_via_langgraph(system: str, user: str, *, max_tokens: int = 1000) -> str:
    response = _client(max_tokens).invoke([
        SystemMessage(content=system),
        HumanMessage(content=user),
    ])
    return _text_content(response.content)
