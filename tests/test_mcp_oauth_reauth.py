"""A connected HTTP MCP server whose token stops working must not hang a turn.

The MCP SDK answers a 401 with a fresh browser authorization inside the
request, and only refreshes ahead of time when it knows the token's expiry,
which it keeps in memory alone. Odysseus restores the stored expiry, and fails
a tool call fast instead of waiting for a sign-in nobody was asked for.
"""
import asyncio
import json
import time

import httpx
import pytest

from src import mcp_oauth

URL = "https://srv.example/mcp"


class _MemoryStorage:
    def __init__(self, tokens=None, expiry=None, client_info=None):
        self.tokens = tokens
        self.expiry = expiry
        self.client_info = client_info
        self.saved = []

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens
        self.saved.append(tokens)

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info

    def token_expiry_time(self):
        return self.expiry


def _provider(monkeypatch, storage, server_id="srv"):
    monkeypatch.setattr(mcp_oauth, "DbTokenStorage", lambda _sid: storage)
    return mcp_oauth.build_provider(server_id, URL)


def _client_info():
    from mcp.shared.auth import OAuthClientInformationFull

    return OAuthClientInformationFull(
        client_id="odysseus-client",
        redirect_uris=[mcp_oauth.REDIRECT_URI],
        token_endpoint_auth_method="none",
    )


def test_stored_expiry_triggers_refresh_before_the_request(monkeypatch):
    from mcp.shared.auth import OAuthToken

    storage = _MemoryStorage(
        tokens=OAuthToken(access_token="old", token_type="Bearer", expires_in=600, refresh_token="r1"),
        expiry=time.time() - 5,
        client_info=_client_info(),
    )
    provider = _provider(monkeypatch, storage)
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/token":
            return httpx.Response(200, json={
                "access_token": "new", "token_type": "Bearer", "expires_in": 600, "refresh_token": "r2",
            })
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=provider) as client:
            return await client.post(URL, json={})

    response = asyncio.run(go())
    assert response.status_code == 200
    assert [(m, p) for m, p, _ in seen] == [("POST", "/token"), ("POST", "/mcp")]
    assert seen[1][2] == "Bearer new"
    assert storage.tokens.access_token == "new"


def test_unexpired_stored_token_is_used_without_refresh(monkeypatch):
    from mcp.shared.auth import OAuthToken

    storage = _MemoryStorage(
        tokens=OAuthToken(access_token="live", token_type="Bearer", expires_in=600, refresh_token="r1"),
        expiry=time.time() + 300,
        client_info=_client_info(),
    )
    provider = _provider(monkeypatch, storage)
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=provider) as client:
            return await client.post(URL, json={})

    assert asyncio.run(go()).status_code == 200
    assert paths == ["/mcp"]


def test_db_storage_reports_expiry_from_when_tokens_were_saved():
    from mcp.shared.auth import OAuthToken

    class Srv:
        oauth_tokens = None

    srv = Srv()

    class Query:
        def filter(self, *a):
            return self

        def first(self):
            return srv

    class Session:
        def query(self, *a):
            return Query()

        def commit(self):
            pass

        def close(self):
            pass

    storage = mcp_oauth.DbTokenStorage("srv", session_factory=Session)
    before = time.time()
    asyncio.run(storage.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=600)))
    expiry = storage.token_expiry_time()
    assert before + 600 <= expiry <= time.time() + 600
    assert json.loads(srv.oauth_tokens)["tokens"]["access_token"] == "a"


def test_connected_server_fails_fast_instead_of_waiting_for_a_browser():
    mcp_oauth.allow_interactive_auth("srv-fast", False)
    provider = mcp_oauth.build_provider("srv-fast", URL)

    async def go():
        await provider.context.redirect_handler("https://auth.example/authorize?state=s-fast")
        started = time.monotonic()
        with pytest.raises(mcp_oauth.McpReauthRequired):
            await provider.context.callback_handler()
        return time.monotonic() - started

    assert asyncio.run(go()) < 1
    assert "s-fast" not in mcp_oauth._pending
    assert mcp_oauth.pop_auth_url("srv-fast") is None


def test_connecting_server_still_waits_for_the_browser_callback():
    mcp_oauth.allow_interactive_auth("srv-wait", True)
    try:
        provider = mcp_oauth.build_provider("srv-wait", URL)

        async def go():
            await provider.context.redirect_handler("https://auth.example/authorize?state=s-wait")
            waiting = asyncio.ensure_future(provider.context.callback_handler())
            await asyncio.sleep(0.05)
            assert not waiting.done()
            assert mcp_oauth.resolve_pending("s-wait", "code-1")
            return await asyncio.wait_for(waiting, timeout=1)

        assert asyncio.run(go()) == ("code-1", "s-wait")
    finally:
        mcp_oauth.allow_interactive_auth("srv-wait", False)


def test_call_tool_returns_auth_required_when_the_token_is_rejected_mid_call():
    from src.mcp_manager import McpManager

    class HangingSession:
        async def call_tool(self, *_a, **_k):
            await asyncio.sleep(3600)

    async def go():
        manager = McpManager()
        manager._sessions["srv"] = HangingSession()
        manager._reauth_events["srv"] = asyncio.Event()
        manager._connections["srv"] = {"status": "connected", "name": "Example Tools", "transport": "http"}
        call = asyncio.ensure_future(manager.call_tool("mcp__srv__list_things", {}))
        await asyncio.sleep(0.05)
        manager._reauth_events["srv"].set()
        result = await asyncio.wait_for(call, timeout=1)
        return manager, result

    manager, result = asyncio.run(go())
    assert result["auth_required"] is True and result["exit_code"] == 1
    assert "Example Tools needs you to sign in again" in result["error"]
    assert "srv" not in manager._sessions
    assert manager._connections["srv"]["status"] == "error"
    # Later calls report the disconnect instead of hanging.
    later = asyncio.run(manager.call_tool("mcp__srv__list_things", {}))
    assert later["exit_code"] == 1
