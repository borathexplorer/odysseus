"""An MCP tool its server declares read-only is not treated as unknown.

Every third-party MCP tool used to get worst-case capabilities, so once skills
or the MCP catalog were in the prompt each call needed an approval. A tool the
server annotates readOnlyHint (and not destructiveHint) is now a known private
read: it runs under control-plane context and is still gated after real
external content. Tool names never count as a declaration.
"""
import pytest

from src.tool_capabilities import (
    ToolEffect, ToolRunSecurityContext, capabilities_for_action,
)

READ = "mcp__srv__list_devices"
WRITE = "mcp__srv__delete_device"
UNANNOTATED = "mcp__srv__get_status"
CONTRADICTORY = "mcp__srv__get_and_wipe"


@pytest.fixture
def annotated_server():
    from src.mcp_manager import McpManager
    from src.tool_utils import get_mcp_manager, set_mcp_manager

    previous = get_mcp_manager()
    manager = McpManager()
    manager._tools["srv"] = [
        {"name": "list_devices", "annotations": {"readOnlyHint": True}},
        {"name": "delete_device", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
        {"name": "get_status", "annotations": None},
        {"name": "get_and_wipe", "annotations": {"readOnlyHint": True, "destructiveHint": True}},
    ]
    set_mcp_manager(manager)
    try:
        yield manager
    finally:
        set_mcp_manager(previous)


def test_declared_read_only_tool_is_a_known_private_read(annotated_server):
    caps = capabilities_for_action(READ, "{}")
    assert caps.known is True
    assert caps.effects == frozenset({ToolEffect.READ_PRIVATE})


@pytest.mark.parametrize("tool", [WRITE, UNANNOTATED, CONTRADICTORY])
def test_other_mcp_tools_stay_unknown(annotated_server, tool):
    # get_status reads by name but declares nothing: names never relax the gate.
    assert capabilities_for_action(tool, "{}").known is False


def test_pydantic_style_annotations_are_read(annotated_server):
    class Ann:
        readOnlyHint = True
        destructiveHint = None

    annotated_server._tools["srv"].append({"name": "show_pools", "annotations": Ann()})
    assert capabilities_for_action("mcp__srv__show_pools", "{}").known is True


def _context(*sources):
    ctx = ToolRunSecurityContext()
    ctx.external_untrusted_context_seen = True
    ctx.external_sources.extend(sources)
    return ctx


def test_control_plane_context_allows_declared_read_without_approval(annotated_server):
    ctx = _context("skills", "mcp tools")
    assert ctx.decision_for(READ, "{}").allowed is True
    assert ctx.decision_for(UNANNOTATED, "{}").allowed is False


def test_real_external_content_still_gates_declared_reads(annotated_server):
    ctx = _context("skills", "web page")
    assert ctx.decision_for(READ, "{}").allowed is False
