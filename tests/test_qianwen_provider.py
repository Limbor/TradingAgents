"""Qianwen wire contract, reasoning handoff and task channel isolation."""

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import ValidationError

from tradingagents.core.model_policy import (
    TaskModelSelection,
    provider_kwargs,
    resolve_model,
    task_model_config,
)
from tradingagents.llm_clients import create_llm_client


@pytest.mark.parametrize("thinking", [False, True])
def test_qianwen_native_tool_roundtrip_keeps_reasoning_and_uses_own_key(monkeypatch, thinking):
    monkeypatch.setenv("QIANWEN_API_KEY", "qianwen-test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "unrelated-dashscope-key")
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == "https://maas.qianwenaiapi.com/compatible-mode/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer qianwen-test-key"
        assert body["enable_thinking"] == thinking
        if len(requests) == 1:
            message = {"role": "assistant", "content": "", "reasoning_content": "需要核对价格",
                       "tool_calls": [{"id": "price-1", "type": "function", "function": {
                           "name": "get_price", "arguments": '{"symbol":"600667.SH"}'}}]}
        else:
            assert body["messages"][-2]["reasoning_content"] == "需要核对价格"
            assert body["messages"][-1]["tool_call_id"] == "price-1"
            message = {"role": "assistant", "content": "已核对工具数据"}
        return httpx.Response(200, json={"id": "chat-1", "object": "chat.completion", "created": 1,
            "model": "qwen3.8-flash", "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        llm = create_llm_client("qianwen", "qwen3.8-flash", http_client=http,
                               **provider_kwargs({"llm_provider": "qianwen", "qianwen_thinking": thinking})).get_llm()
        llm = llm.bind_tools([{"name": "get_price", "description": "price", "parameters": {
            "type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}])
        messages = [HumanMessage(content="查询太极实业价格")]
        response = llm.invoke(messages)
        assert response.tool_calls[0]["args"] == {"symbol": "600667.SH"}
        messages.extend([response, ToolMessage(content='{"price":10}', tool_call_id="price-1")])
        assert llm.invoke(messages).content == "已核对工具数据"
    assert len(requests) == 2


def test_chat_model_cannot_reuse_other_channel_endpoint_or_inherited_deep_model(monkeypatch):
    monkeypatch.setenv("QIANWEN_API_KEY", "test-key")
    config = {"llm_provider": "deepseek", "backend_url": "https://api.deepseek.com",
              "model_policy": {"default_model": "deepseek-flash", "deep_model": "deepseek-v4-pro"}}
    snapshot = task_model_config(config, {"provider": "qianwen", "model": "qwen3.8-flash"})
    assert snapshot["backend_url"] is None
    assert snapshot["llm_provider"] == "qianwen"
    assert resolve_model(snapshot, "deep") == "qwen3.8-flash"
    assert config["llm_provider"] == "deepseek"
    assert config["model_policy"]["deep_model"] == "deepseek-v4-pro"


def test_unconfigured_channel_rejected_without_falling_back_to_other_key(monkeypatch):
    monkeypatch.delenv("QIANWEN_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "another-account")
    with pytest.raises(ValueError, match="QIANWEN_API_KEY"):
        task_model_config({}, {"provider": "qianwen", "model": "qwen3.8-flash"})
    with pytest.raises(ValueError, match="不支持"):
        task_model_config({}, {"provider": "unknown", "model": "test"})
    with pytest.raises(ValidationError):
        TaskModelSelection(provider="qianwen", model="qwen3.8-flash", base_url="https://evil.example")


def test_qianwen_structured_output_does_not_force_tool_choice(monkeypatch):
    monkeypatch.setenv("QIANWEN_API_KEY", "test-key")
    llm = create_llm_client("qianwen", "qwen3.7-plus").get_llm()
    structured = llm.with_structured_output({"title": "Decision", "type": "object",
        "properties": {"summary": {"type": "string"}}, "required": ["summary"]})
    assert "tool_choice" not in structured.first.kwargs
