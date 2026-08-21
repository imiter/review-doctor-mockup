"""app/llm/client.py 자체를 검증하는 테스트. 다른 llm 테스트들(test_llm_classify.py,
test_llm_generate.py)은 전부 client.call_haiku/call_sonnet을 monkeypatch해서 쓰기
때문에, 실제 SDK 응답을 파싱하는 이 파일의 코드 경로(_first_text)는 그동안
어디서도 검증된 적이 없었다 — Sonnet 5는 기본으로 adaptive thinking이 켜져 있어
response.content[0]이 텍스트가 아닌 ThinkingBlock일 수 있는데, 이 케이스를
스캔해서 건너뛰는지가 이 파일의 핵심 검증 대상이다."""

import pytest

from app.llm import client


class _FakeThinkingBlock:
    type = "thinking"
    # ThinkingBlock에는 .text 속성이 없다 — 실제 SDK 타입과 동일하게 재현.


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, content: list):
        self.content = content


class _FakeMessages:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response, *, api_key=None, timeout=None):
        self.messages = _FakeMessages(response)


def _install_fake_client(monkeypatch, response):
    """anthropic.Anthropic 생성자를 가짜로 바꿔치기해서, 실제 네트워크 호출
    없이 client._client()가 만든 인스턴스가 우리가 만든 응답을 돌려주게
    한다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        client.anthropic, "Anthropic",
        lambda **kw: _FakeAnthropicClient(response, **kw),
    )


def test_call_sonnet_skips_thinking_block_and_returns_text(monkeypatch):
    """실제 Sonnet 응답처럼 content[0]이 ThinkingBlock, content[1]이
    TextBlock인 경우 — content[0]을 그냥 인덱싱했다면 AttributeError가
    났을 자리다."""
    response = _FakeMessage([_FakeThinkingBlock(), _FakeTextBlock("실제 답글")])
    _install_fake_client(monkeypatch, response)

    result = client.call_sonnet("system", "user")

    assert result == "실제 답글"


def test_call_sonnet_raises_when_no_text_block_present(monkeypatch):
    response = _FakeMessage([_FakeThinkingBlock()])
    _install_fake_client(monkeypatch, response)

    with pytest.raises(RuntimeError):
        client.call_sonnet("system", "user")


def test_call_haiku_with_only_text_block_still_works(monkeypatch):
    """회귀 테스트 — thinking 블록이 없는 일반적인(현재 테스트 스위트가
    가정해온) 응답 형태가 여전히 정상 동작하는지 확인."""
    response = _FakeMessage([_FakeTextBlock("하이쿠 답글")])
    _install_fake_client(monkeypatch, response)

    result = client.call_haiku("system", "user")

    assert result == "하이쿠 답글"


def test_call_sonnet_passes_thinking_disabled(monkeypatch):
    """call_sonnet은 thinking={"type": "disabled"}를 명시적으로 넘겨야 한다
    — Sonnet 5는 기본으로 adaptive thinking이 켜져 있어서 생략하면
    max_tokens가 thinking+응답 합산 예산이 되어버리기 때문(Finding 2)."""
    captured = {}

    class _CapturingMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeMessage([_FakeTextBlock("ok")])

    class _CapturingClient:
        def __init__(self, **kw):
            self.messages = _CapturingMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(client.anthropic, "Anthropic", lambda **kw: _CapturingClient(**kw))

    client.call_sonnet("system", "user")

    assert captured.get("thinking") == {"type": "disabled"}


def test_call_haiku_does_not_pass_thinking_param(monkeypatch):
    """Haiku는 기본으로 thinking이 켜지지 않으므로 thinking 파라미터를
    넘기면 안 된다(Sonnet 전용 변경)."""
    captured = {}

    class _CapturingMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeMessage([_FakeTextBlock("ok")])

    class _CapturingClient:
        def __init__(self, **kw):
            self.messages = _CapturingMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(client.anthropic, "Anthropic", lambda **kw: _CapturingClient(**kw))

    client.call_haiku("system", "user")

    assert "thinking" not in captured


def test_client_constructed_with_timeout(monkeypatch):
    """_client()가 SDK 기본 타임아웃(수 분+재시도)이 아니라 60초 타임아웃을
    명시적으로 넘기는지 확인(Finding 2 — degraded API에서 무한정 오래
    걸리는 걸 방지)."""
    captured_kwargs = {}

    def _fake_anthropic(**kwargs):
        captured_kwargs.update(kwargs)
        return _FakeAnthropicClient(_FakeMessage([_FakeTextBlock("ok")]), **kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(client.anthropic, "Anthropic", _fake_anthropic)

    client.call_haiku("system", "user")

    assert captured_kwargs.get("timeout") == 60.0
