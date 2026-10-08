"""An unproven context window must not be budgeted like a tiny local server.

``budget_context_for_model`` returns 0 when a model's window is not known. The
small-server clamp (``max(1200, ctx * 0.40)``) used to treat that 0 as a tiny
window and trim every route to ~1800 tokens, dropping the agent prompt and
tool catalogs so the model answered as if it had no tools.
"""
import json

import pytest


DONE = 'data: [DONE]\n\n'


async def _trim_window_for_context(monkeypatch, context_window):
    import src.agent_loop as al
    import src.context_compactor as cc
    from tests.test_agent_runtime_context import _patch_fake_skills

    _patch_fake_skills(monkeypatch)
    monkeypatch.setattr('src.tool_index.get_tool_index', lambda: None)
    monkeypatch.setattr(
        'src.model_context.budget_context_for_model',
        lambda *args, **kwargs: context_window,
    )
    windows = []
    real_trim = cc.trim_for_context

    def spy(messages, context_length, *args, **kwargs):
        windows.append(context_length)
        return real_trim(messages, context_length, *args, **kwargs)

    monkeypatch.setattr(cc, 'trim_for_context', spy)

    async def provider(*args, **kwargs):
        yield 'data: ' + json.dumps({'delta': 'ok'}) + '\n\n'
        yield DONE

    monkeypatch.setattr(al, 'stream_llm_with_fallback', provider)
    async for _ in al.stream_agent_loop(
        'https://api.openai.com/v1', 'unlisted-model',
        [{'role': 'user', 'content': 'Explain why a parser should normalize inputs.'}],
        max_rounds=1,
    ):
        pass
    assert windows, 'route budget path did not trim'
    return windows[0]


@pytest.mark.asyncio
async def test_unknown_context_window_uses_default_budget_not_small_server_floor(monkeypatch):
    from src.context_budget import DEFAULT_BUDGET

    window = await _trim_window_for_context(monkeypatch, 0)
    assert window >= DEFAULT_BUDGET


@pytest.mark.asyncio
async def test_known_small_context_window_keeps_small_server_headroom(monkeypatch):
    window = await _trim_window_for_context(monkeypatch, 4096)
    assert window <= 4096
    assert window < 4096 * 0.40 + 2048
