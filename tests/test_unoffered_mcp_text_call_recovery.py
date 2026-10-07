"""Native routes must not silently drop a textual call to an enabled MCP tool.

On a native route (`_is_api_model=True`, e.g. Qwen3 on an Ollama endpoint with
`supports_tools=true`) only the retrieval-selected subset of MCP schemas is
sent. Qwen3-30B was asked about a noisy disk, knew the LXD tool name from the
conversation, and wrote it out as text three times:

    mcp__1c094139__lxd_list_storage_pools

The parser recovered the call, but `_resolve_tool_blocks` dropped it as
"unoffered", so nothing ran and the narration became the final answer. A
textual route would have run it, so an enabled MCP tool is recoverable on a
native route too. Disabled / unknown MCP names stay dropped.
"""
import asyncio
import json

import src.agent_loop as al
from src.tool_capabilities import ToolGateDecision

LXD_POOLS = "mcp__1c094139__lxd_list_storage_pools"
LXD_DISABLED = "mcp__1c094139__lxd_delete_instance"

QWEN_OUTPUT = (
    "I'll help you investigate the unusual noise from the large data disk on server-1.\n\n"
    "First, let's retrieve the current disk and storage pool information:\n\n"
    f"{LXD_POOLS}\n"
    "This will show us details about your LXD storage pools.\n\n"
    "Let me fetch the current storage pool information for you.\n\n"
    f"{LXD_POOLS}\n"
    "This will show us details about your LXD storage pools.\n\n"
    "Let me fetch the current storage pool information for you.\n\n"
    f"{LXD_POOLS}\n"
)


def _schema(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "LXD tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _FakeMcpManager:
    def __init__(self, names):
        self._names = list(names)

    def get_all_openai_schemas(self, disabled_map=None):
        return [_schema(name) for name in self._names]

    def __getattr__(self, _name):
        # Anything else the loop probes (status, prompts, ...) is a no-op.
        return lambda *a, **k: None


def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _run_loop(monkeypatch, text, mcp_names):
    exec_calls = []
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: _FakeMcpManager(mcp_names), raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)
    monkeypatch.setattr(
        al.ToolRunSecurityContext,
        "decision_for",
        lambda self, *a, **k: ToolGateDecision(True),
        raising=False,
    )

    async def _fake_exec(block, *a, **k):
        exec_calls.append(block)
        return (block.tool_type, {"output": "pool: default (zfs)", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    call_count = {"n": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        call_count["n"] += 1
        reply = text if call_count["n"] == 1 else "The pool looks healthy."
        yield f'data: {json.dumps({"delta": reply})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "why is the large data disk on my server-1 making so much noise?"}],
        max_rounds=2,
        # Retrieval picked a tool set without the LXD MCP tool.
        relevant_tools={"web_search"},
        owner="admin",
    ))
    return exec_calls


def test_native_route_runs_textual_call_to_enabled_unoffered_mcp_tool(monkeypatch):
    exec_calls = _run_loop(monkeypatch, QWEN_OUTPUT, [LXD_POOLS])
    assert [b.tool_type for b in exec_calls] == [LXD_POOLS]


def test_native_route_still_drops_textual_call_to_unknown_mcp_tool(monkeypatch):
    exec_calls = _run_loop(monkeypatch, QWEN_OUTPUT.replace(LXD_POOLS, LXD_DISABLED), [LXD_POOLS])
    assert exec_calls == []


def test_resolve_tool_blocks_recovers_only_listed_unoffered_names():
    blocks, used_native, _ = al._resolve_tool_blocks(
        QWEN_OUTPUT, [], 1,
        is_api_model=True,
        offered_tool_names={"web_search"},
        recover_unoffered_tool_names={LXD_POOLS},
    )
    assert [b.tool_type for b in blocks] == [LXD_POOLS]
    assert used_native is False

    blocks, _, _ = al._resolve_tool_blocks(
        QWEN_OUTPUT, [], 1,
        is_api_model=True,
        offered_tool_names={"web_search"},
        recover_unoffered_tool_names=None,
    )
    assert blocks == []
