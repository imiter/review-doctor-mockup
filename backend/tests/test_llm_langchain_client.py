from unittest.mock import MagicMock, patch

from app.llm.langchain_client import call_sonnet_via_langgraph


def test_call_sonnet_via_langgraph_returns_text_content(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.content = "테스트 응답"

    with patch("app.llm.langchain_client.ChatAnthropic") as MockChatAnthropic:
        MockChatAnthropic.return_value.invoke.return_value = fake_response
        result = call_sonnet_via_langgraph("시스템 프롬프트", "사용자 메시지")

    assert result == "테스트 응답"
    MockChatAnthropic.return_value.invoke.assert_called_once()


def test_call_sonnet_via_langgraph_handles_block_list_content(monkeypatch):
    """langchain_anthropic이 content를 문자열이 아니라 블록 dict 리스트로
    돌려주는 경우(citations가 붙은 단일 블록, 또는 여러 블록)도 처리해야
    한다 — 실제 라이브러리 소스(_format_output)에서 확인된 두 번째 응답
    모양."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.content = [{"type": "text", "text": "블록 응답"}]

    with patch("app.llm.langchain_client.ChatAnthropic") as MockChatAnthropic:
        MockChatAnthropic.return_value.invoke.return_value = fake_response
        result = call_sonnet_via_langgraph("시스템 프롬프트", "사용자 메시지")

    assert result == "블록 응답"


def test_call_sonnet_via_langgraph_passes_system_and_user_messages(monkeypatch):
    """SystemMessage/HumanMessage로 올바르게 감싸 invoke에 넘기는지 확인."""
    from langchain_core.messages import HumanMessage, SystemMessage

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.content = "응답"

    with patch("app.llm.langchain_client.ChatAnthropic") as MockChatAnthropic:
        MockChatAnthropic.return_value.invoke.return_value = fake_response
        call_sonnet_via_langgraph("시스템 프롬프트", "사용자 메시지", max_tokens=500)

    call_args = MockChatAnthropic.return_value.invoke.call_args
    messages = call_args[0][0]
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == "시스템 프롬프트"
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "사용자 메시지"

    MockChatAnthropic.assert_called_once_with(
        model="claude-sonnet-5", api_key="test-key",
        max_tokens=500, timeout=60.0,
        thinking={"type": "disabled"},
    )
