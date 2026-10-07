"""Owner-enabled third-party MCP tools are offered and admitted on chat turns.

Request authority grants tools from request families, and no family describes
a user-installed MCP server, so its tools were never offered by the turn
contract and every call failed with "The exact operation is outside server
request authority." -- even "Call get_server_time" for an enabled tool.

These tests drive the real registered chat route with a real McpManager seeded
with discovered tool metadata; only session/context/inference are stubbed. The
admission check uses the authority the route hands to the agent loop.
"""
import pytest

from src.agent_runtime.authority import (
    ExactOperation, OperationGrant, RequestAuthority,
)
from src.tool_policy import ToolPolicy
from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint

SERVER_TIME = "mcp__srv__get_server_time"
LIST_INSTANCES = "mcp__srv__list_instances"
DELETE_INSTANCE = "mcp__srv__delete_instance"


@pytest.fixture
def user_mcp_server():
    from src.mcp_manager import McpManager
    from src.tool_utils import get_mcp_manager, set_mcp_manager

    previous = get_mcp_manager()
    manager = McpManager()
    # Discovered metadata only; nothing connects to or executes a server.
    manager._tools["srv"] = [
        {"name": name, "description": f"{name} tool",
         "input_schema": {"type": "object", "properties": {}}}
        for name in ("get_server_time", "list_instances", "delete_instance")
    ]
    set_mcp_manager(manager)
    try:
        yield manager
    finally:
        set_mcp_manager(previous)


async def _route_turn(monkeypatch, message, *, plan_mode=False, disabled_map=None):
    from routes import chat_routes
    from src import agent_loop, tool_security

    endpoint = _chat_stream_endpoint(monkeypatch, "agent", {})
    monkeypatch.setattr(chat_routes, "coerce_message_and_session",
                        lambda *args, **kwargs: (message, "session-1"))
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda owner: True)
    monkeypatch.setattr(agent_loop, "_load_mcp_disabled_map", lambda: dict(disabled_map or {}))

    observed = []

    async def capture_agent(*args, **kwargs):
        observed.append((kwargs["turn_contract"], kwargs["request_authority"]))
        yield 'data: {"delta":"ok"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form["message"] = message
    if plan_mode:
        request._form["plan_mode"] = "true"
    response = await endpoint(request)
    assert response.status_code == 200
    [chunk async for chunk in response.body_iterator]
    assert len(observed) == 1
    return observed[0]


def _admits(authority, tool):
    return authority.permits(ExactOperation.normalize(tool, "{}"))


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "Call get_server_time. Return only the actual tool result.",
    "why is the big disk on my server so noisy for the last 5 hours?",
])
async def test_enabled_mcp_tools_are_offered_and_admitted(monkeypatch, user_mcp_server, message):
    contract, authority = await _route_turn(monkeypatch, message)
    for tool in (SERVER_TIME, LIST_INSTANCES, DELETE_INSTANCE):
        assert tool in contract.offered
        assert contract.permits(tool)
        assert _admits(authority, tool)


@pytest.mark.asyncio
async def test_disabled_mcp_tool_stays_unavailable(monkeypatch, user_mcp_server):
    contract, authority = await _route_turn(
        monkeypatch, "Call get_server_time.", disabled_map={"srv": {"delete_instance"}},
    )
    assert SERVER_TIME in contract.offered and _admits(authority, SERVER_TIME)
    assert DELETE_INSTANCE not in contract.offered
    assert not _admits(authority, DELETE_INSTANCE)


@pytest.mark.asyncio
async def test_plan_mode_offers_no_user_mcp_tools(monkeypatch, user_mcp_server):
    contract, authority = await _route_turn(monkeypatch, "Call get_server_time.", plan_mode=True)
    assert not any(name.startswith("mcp__srv__") for name in contract.offered)
    assert not _admits(authority, SERVER_TIME)


@pytest.mark.asyncio
async def test_exact_operation_turn_keeps_its_narrow_contract(monkeypatch, user_mcp_server):
    contract, authority = await _route_turn(monkeypatch, "List my calendar events")
    assert contract.required_read_operation is not None
    assert not any(name.startswith("mcp__srv__") for name in contract.offered)
    assert not _admits(authority, SERVER_TIME)


def test_with_tools_grants_but_restrict_still_denies():
    authority = RequestAuthority("id", "admin", "s", "", (OperationGrant("ask_user"),))
    granted = authority.with_tools({SERVER_TIME, DELETE_INSTANCE, "ask_user"})
    assert sorted(g.tool for g in granted.grants) == ["ask_user", DELETE_INSTANCE, SERVER_TIME]
    assert _admits(granted, SERVER_TIME)
    denied = granted.restrict(disabled_tools={DELETE_INSTANCE})
    assert not _admits(denied, DELETE_INSTANCE)
    assert _admits(denied, SERVER_TIME)
    no_mcp = granted.restrict(ToolPolicy(disable_mcp=True))
    assert not _admits(no_mcp, SERVER_TIME)
    assert _admits(no_mcp, "ask_user")
    assert authority.with_tools(()) is authority
