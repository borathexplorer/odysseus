"""Text-transport routes must be told how to call the owner's MCP tools.

Routes without native tool schemas (ChatGPT subscription, Ollama without
native tools) only saw MCP tools inside the untrusted "MCP tools" catalog,
which says not to call tools because of its content and claimed the tools
were "called via native function calling". Models therefore answered that
only manage_settings/ask_user were available. A trusted directive now names
the offered MCP tools and a <tool_call> format the parser accepts.
"""
import asyncio
import json

import pytest

import src.agent_loop as al
from src.tool_capabilities import ToolGateDecision

SERVER_TIME = "mcp__srv__get_server_time"
LIST_INSTANCES = "mcp__srv__list_instances"


class _FakeMcpManager:
    def __init__(self, names):
        self._names = list(names)

    def get_all_openai_schemas(self, disabled_map=None):
        return [{
            "type": "function",
            "function": {
                "name": name,
                "description": "[MCP:srv] cluster tool",
                "parameters": {"type": "object", "properties": {}},
            },
        } for name in self._names]

    def get_tool_descriptions_for_prompt(self, disabled_map=None, allowed_names=None):
        return "\n\nYou also have access to external MCP tool servers. Their tools:\n" + "\n".join(
            f"  - {name}: cluster tool" for name in self._names
        )

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _run(monkeypatch, endpoint_url, model, reply):
    exec_calls, requests = [], []
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(
        al, "get_mcp_manager", lambda: _FakeMcpManager([SERVER_TIME, LIST_INSTANCES]), raising=False
    )
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)
    monkeypatch.setattr(
        al.ToolRunSecurityContext, "decision_for",
        lambda self, *a, **k: ToolGateDecision(True), raising=False,
    )

    async def _fake_exec(block, *a, **k):
        exec_calls.append(block)
        return (block.tool_type, {"output": "2026-10-08T02:30:00Z", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append((messages, kwargs.get("tools")))
        text = reply if len(requests) == 1 else "2026-10-08T02:30:00Z"
        yield f'data: {json.dumps({"delta": text})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(al.stream_agent_loop(
        endpoint_url, model,
        [{"role": "user", "content": "Call get_server_time. Return only the actual tool result."}],
        max_rounds=2,
        # Production passes the turn contract's offered set; tool RAG is not
        # available in unit tests.
        relevant_tools={SERVER_TIME, LIST_INSTANCES, "web_search"},
        owner="admin",
    ))
    return exec_calls, requests


def _system_text(messages):
    return "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")


def test_textual_route_gets_trusted_mcp_directive_and_runs_the_call(monkeypatch):
    call = '<tool_call>{"name": "%s", "arguments": {}}</tool_call>' % SERVER_TIME
    exec_calls, requests = _run(monkeypatch, "http://127.0.0.1:11434/v1", "qwen3:30b", call)
    messages, tools = requests[0]
    assert not tools
    system = _system_text(messages)
    assert "<tool_call>" in system
    assert f"- {SERVER_TIME}" in system and f"- {LIST_INSTANCES}" in system
    assert "called via native function calling" not in json.dumps(messages)
    # A textual route must not be told to use native schemas it never receives.
    assert "Use only the native tool schemas" not in system
    assert [b.tool_type for b in exec_calls] == [SERVER_TIME]


def test_native_route_gets_schemas_not_the_textual_directive(monkeypatch):
    _exec_calls, requests = _run(monkeypatch, "https://api.openai.com/v1", "gpt-4o", "ok")
    messages, tools = requests[0]
    assert SERVER_TIME in {t["function"]["name"] for t in tools or []}
    assert f"- {SERVER_TIME}" not in _system_text(messages)


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-5.6-sol"])
def test_chatgpt_subscription_route_calls_mcp_tools_textually(monkeypatch, model):
    # The subscription route never carries native tool schemas; Odysseus' text
    # protocol is the only way its models can reach MCP tools.
    call = '<tool_call>{"name": "%s", "arguments": {}}</tool_call>' % SERVER_TIME
    exec_calls, requests = _run(
        monkeypatch, "https://chatgpt.com/backend-api/codex", model, call
    )
    messages, tools = requests[0]
    assert not tools
    system = _system_text(messages)
    assert "<tool_call>" in system and f"- {SERVER_TIME}" in system
    assert "Use only the native tool schemas" not in system
    assert [b.tool_type for b in exec_calls] == [SERVER_TIME]
    # The tool result reaches the model on the next round.
    assert len(requests) == 2
    assert "2026-10-08T02:30:00Z" in json.dumps(requests[1][0])
