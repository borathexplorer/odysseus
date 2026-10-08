"""Ollama's served context, not the model's trained window, bounds a prompt.

Ollama runs models with its own context length (OLLAMA_CONTEXT_LENGTH, 4096
by default) and silently truncates longer prompts. Claiming the trained window
from the known-models table let the agent send ~9k-token prompts to a
4k-token server, so local models never saw the user's question.
"""
import httpx

from src import model_context

URL = "http://127.0.0.1:11434/v1"
QWEN = "hf.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:UD-Q4_K_XL"


def _serve(monkeypatch, routes):
    calls = []

    def fake_get(url, *args, **kwargs):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        calls.append(path)
        body = routes.get(path)
        if body is None:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(model_context.httpx, "get", fake_get)
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda url: None)
    model_context._context_cache.clear()
    return calls


def test_loaded_ollama_model_reports_served_context(monkeypatch):
    _serve(monkeypatch, {"/api/ps": {"models": [
        {"name": "gpt-oss:20b", "model": "gpt-oss:20b", "context_length": 8192},
        {"name": QWEN, "model": QWEN, "context_length": 4096},
    ]}})
    assert model_context._query_context_length(URL, QWEN) == (4096, True)
    assert model_context.budget_context_for_model(URL, QWEN) == 4096


def test_unloaded_ollama_model_does_not_claim_trained_window(monkeypatch):
    _serve(monkeypatch, {"/api/ps": {"models": []}, "/v1/models": {"data": [{"id": QWEN}]}})
    ctx, known = model_context._query_context_length(URL, QWEN)
    assert known is False
    assert model_context.budget_context_for_model(URL, QWEN) == 0


def test_llamacpp_slots_still_take_precedence(monkeypatch):
    calls = _serve(monkeypatch, {"/slots": [{"n_ctx": 16384}],
                                 "/api/ps": {"models": [{"name": QWEN, "context_length": 4096}]}})
    assert model_context._query_context_length(URL, QWEN) == (16384, True)
    assert "/api/ps" not in calls


def test_non_ollama_local_server_keeps_known_table(monkeypatch):
    _serve(monkeypatch, {"/v1/models": {"data": [{"id": QWEN}]}})
    assert model_context._query_context_length(URL, QWEN) == (131072, True)
