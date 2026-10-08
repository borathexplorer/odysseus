"""ChatGPT Subscription models are budgeted by the window their catalog reports.

gpt-6.x is not in the known-models table, so the agent fell back to the
6000-token default budget and trimmed MCP results before the model saw them.
The live Codex catalog reports `context_window` for every model.
"""
import httpx
import pytest

from src import chatgpt_subscription as cs
from src import model_context

URL = "https://chatgpt.com/backend-api/codex"
CATALOG = {"models": [
    {"slug": "gpt-6.1-sol", "context_window": 272000, "max_context_window": 872000,
     "supported_reasoning_levels": [{"effort": "medium"}], "priority": 1},
    {"slug": "gpt-5.5", "context_window": 272000, "max_context_window": 272000, "priority": 2},
    {"slug": "odd-model", "context_window": "big", "priority": 3},
]}


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(cs, "CHATGPT_MODEL_CONTEXT_WINDOWS", {})
    monkeypatch.setattr(cs.httpx, "get", lambda *a, **k: httpx.Response(
        200, json=CATALOG, request=httpx.Request("GET", "https://chatgpt.com/backend-api/codex/models")))
    model_context._context_cache.clear()
    yield
    model_context._context_cache.clear()


def test_catalog_records_context_windows(catalog):
    assert cs.fetch_available_models("token")[:2] == ["gpt-6.1-sol", "gpt-5.5"]
    assert cs.catalog_context_window("gpt-6.1-sol") == 272000
    assert cs.catalog_context_window("gpt-5.5") == 272000
    assert cs.catalog_context_window("odd-model") is None


def test_subscription_model_budget_uses_catalog_window(catalog, monkeypatch):
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda url: "api")
    # Before the catalog is fetched the window is unknown, and that result is cached.
    assert model_context.budget_context_for_model(URL, "gpt-6.1-sol") == 0
    cs.fetch_available_models("token")
    # The catalog wins over the earlier cached "unknown".
    assert model_context.get_context_length_known(URL, "gpt-6.1-sol") == (272000, True)
    assert model_context.budget_context_for_model(URL, "gpt-6.1-sol") == 272000


def test_catalog_window_is_not_applied_to_other_endpoints(catalog, monkeypatch):
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda url: "api")
    cs.fetch_available_models("token")
    ctx, known = model_context.get_context_length_known("https://api.example.com/v1", "gpt-6.1-sol")
    assert (ctx, known) != (272000, True)
