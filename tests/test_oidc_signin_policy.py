"""Behavioral coverage for local sign-in policy and provider-managed access."""

import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth_policy import (
    OidcPolicyError, oidc_group_policy, oidc_provider_display,
    password_auth_enabled, validate_auth_policy,
)
from tests.helpers.import_state import clear_module, preserve_import_state


@pytest.fixture(autouse=True)
def policy_environment(monkeypatch):
    for key in ("OIDC_ENABLED", "OIDC_ADMIN_GROUPS", "OIDC_PRIVILEGE_GROUPS",
                "OIDC_GROUPS_CLAIM", "OIDC_PROVIDER_NAME", "OIDC_PROVIDER_ICON",
                "PASSWORD_AUTH_ENABLED", "AUTH_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OIDC_FIRST_USER_IS_ADMIN", "false")


@pytest.fixture
def auth_modules(monkeypatch):
    names = ["core.auth", "routes.auth_routes", "routes.oidc_routes"]
    with preserve_import_state(*names):
        for name in names:
            clear_module(name)
        auth = importlib.import_module("core.auth")
        # Keep policy tests quick; actual bcrypt behavior has separate coverage.
        monkeypatch.setattr(auth, "_hash_password", lambda p: "hash:" + p)
        monkeypatch.setattr(auth, "_verify_password", lambda p, h: h == "hash:" + p)
        yield auth, importlib.import_module("routes.auth_routes"), importlib.import_module("routes.oidc_routes")


@pytest.fixture
def manager(auth_modules, tmp_path):
    return auth_modules[0].AuthManager(str(tmp_path / "auth.json"))


@pytest.mark.parametrize("value,enabled", [("true", True), ("1", True), ("yes", True),
                                         ("false", False), ("0", False), ("off", False)])
def test_password_switch(monkeypatch, value, enabled):
    monkeypatch.setenv("PASSWORD_AUTH_ENABLED", value)
    assert password_auth_enabled() is enabled


def test_oidc_only_requires_config_but_not_live_discovery(monkeypatch):
    monkeypatch.setenv("PASSWORD_AUTH_ENABLED", "false")
    with pytest.raises(ValueError, match="requires OIDC"):
        validate_auth_policy()
    for key, value in {"OIDC_ENABLED": "true", "OIDC_ISSUER": "https://idp.example.com",
                       "OIDC_CLIENT_ID": "test", "OIDC_CLIENT_SECRET": "test-only"}.items():
        monkeypatch.setenv(key, value)
    validate_auth_policy()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    with pytest.raises(ValueError, match="remain enabled"):
        validate_auth_policy()


def test_password_disable_blocks_direct_auth_but_preserves_oidc(manager, monkeypatch):
    manager.create_user("local", "password123", is_admin=True)
    monkeypatch.setenv("PASSWORD_AUTH_ENABLED", "false")
    assert not manager.verify_password("local", "password123")
    assert manager.create_session("local", "password123") is None
    assert not manager.create_user("new", "password123")
    assert not manager.change_password("local", "password123", "replacement123")
    assert manager.create_user_oidc("alice", "sub-a", "https://idp.example.com") == "alice"
    assert manager.create_session_trusted("alice")


@pytest.mark.parametrize("path,payload", [
    ("/setup", {"username": "alice", "password": "password123"}),
    ("/signup", {"username": "alice", "password": "password123"}),
    ("/login", {"username": "alice", "password": "password123"}),
    ("/change-password", {"current_password": "password123", "new_password": "replacement123"}),
    ("/users", {"username": "alice", "password": "password123"}),
    ("/signup-toggle", {}),
])
def test_disabled_password_routes_reject_requests(auth_modules, manager, monkeypatch, path, payload):
    monkeypatch.setenv("PASSWORD_AUTH_ENABLED", "false")
    app = FastAPI()
    app.include_router(auth_modules[1].setup_auth_routes(manager))
    with TestClient(app) as client:
        response = client.post("/api/auth" + path, json=payload)
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"]
        status = client.get("/api/auth/status").json()
        assert status["password_auth_enabled"] is False
        assert status["signup_enabled"] is False
        assert client.get("/api/auth/policy").json()["password_auth_enabled"] is False
    assert not manager.users


@pytest.mark.parametrize("groups", [None, "admins", [123], ["admins", {}], [""]])
def test_malformed_groups_never_grant_access(monkeypatch, groups):
    monkeypatch.setenv("OIDC_ADMIN_GROUPS", "admins")
    with pytest.raises(OidcPolicyError):
        oidc_group_policy().grants({"groups": groups})


def test_custom_claim_and_exact_permission_mapping(monkeypatch):
    monkeypatch.setenv("OIDC_GROUPS_CLAIM", "realm_access.roles")
    monkeypatch.setenv("OIDC_ADMIN_GROUPS", "admins")
    monkeypatch.setenv("OIDC_PRIVILEGE_GROUPS", json.dumps({"can_use_bash": ["operators"], "can_use_agent": []}))
    assert oidc_group_policy().grants({"realm_access": {"roles": ["operators", "Admins"]}}) == (
        False, {"can_use_bash": True, "can_use_agent": False},
    )


@pytest.mark.parametrize("mapping", ['[]', '{', '{"is_admin":["admins"]}', '{"can_use_bash":"operators"}'])
def test_invalid_mapping_rejected(monkeypatch, mapping):
    monkeypatch.setenv("OIDC_PRIVILEGE_GROUPS", mapping)
    with pytest.raises(OidcPolicyError):
        oidc_group_policy()


def callback_client(auth_modules, manager, claims):
    oidc = SimpleNamespace(configured=True, issuer="https://idp.example.com", provider_name="Example",
                           redirect_uri_override="https://testserver/api/auth/oidc/callback",
                           exchange_code=lambda *args: dict(claims))
    app = FastAPI()
    app.include_router(auth_modules[2].setup_oidc_routes(manager, oidc))
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    client.cookies.set("odysseus_oidc_csrf", "test-state")
    return client


def test_callback_updates_roles_and_permissions_across_workers(auth_modules, manager, monkeypatch):
    monkeypatch.setenv("OIDC_ADMIN_GROUPS", "admins")
    monkeypatch.setenv("OIDC_PRIVILEGE_GROUPS", '{"can_use_bash":["operators"],"can_use_agent":["agents"]}')
    claims = {"sub": "a", "preferred_username": "alice", "groups": ["admins"]}
    with callback_client(auth_modules, manager, claims) as client:
        assert client.get('/api/auth/oidc/callback?code=test&state=test-state').headers['location'] == '/'
    other = auth_modules[0].AuthManager(manager.auth_path)
    assert other.is_admin("alice")
    old_session = other.create_session_trusted("alice")
    claims["groups"] = ["operators"]
    with callback_client(auth_modules, manager, claims) as client:
        response = client.get('/api/auth/oidc/callback?code=test&state=test-state')
        assert response.headers['location'] == '/'
        assert 'Secure' in response.headers['set-cookie']
    assert other.validate_token(old_session)
    assert not other.is_admin("alice")  # including a last-admin provider demotion
    assert other.get_privileges("alice")["can_use_bash"] is True
    assert other.get_privileges("alice")["can_use_agent"] is False
    claims["groups"] = []
    with callback_client(auth_modules, manager, claims) as client:
        assert client.get('/api/auth/oidc/callback?code=test&state=test-state').headers['location'] == '/'
    assert other.get_privileges("alice")["can_use_bash"] is False


def test_missing_role_claim_refuses_session_without_destroying_stored_role(auth_modules, manager, monkeypatch):
    monkeypatch.setenv("OIDC_ADMIN_GROUPS", "admins")
    manager.create_user_oidc("alice", "a", "https://idp.example.com", is_admin=True)
    with callback_client(auth_modules, manager, {"sub": "a"}) as client:
        response = client.get('/api/auth/oidc/callback?code=test&state=test-state')
        assert 'oidc_claims' in response.headers['location']
        assert 'odysseus_session=' not in response.headers.get('set-cookie', '')
    assert manager.is_admin("alice")


def test_access_sync_cannot_touch_another_identity(manager):
    manager.create_user("local", "password123")
    manager.create_user_oidc("alice", "a", "https://idp.example.com")
    assert not manager.sync_oidc_access("local", "a", "https://idp.example.com", True, {})
    assert not manager.sync_oidc_access("alice", "b", "https://idp.example.com", True, {})
    assert not manager.is_admin("alice")


def test_discovery_recovers_and_throttles_retries(auth_modules, monkeypatch):
    from core import oidc
    routes = auth_modules[2]
    monkeypatch.setenv("OIDC_ENABLED", "true")
    tick = [100.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: tick[0])
    calls = []
    recovered = SimpleNamespace(configured=True, provider_name="Recovered")
    def initialize():
        calls.append(1)
        return None if len(calls) == 1 else recovered
    monkeypatch.setattr(oidc, "init_oidc_manager", initialize)
    app = FastAPI()
    app.include_router(routes.setup_oidc_routes(None, None))
    with TestClient(app) as client:
        assert client.get('/api/auth/oidc/config').json()['enabled'] is False
        assert client.get('/api/auth/oidc/config').json()['enabled'] is False
        assert len(calls) == 1
        tick[0] += 31
        assert client.get('/api/auth/oidc/config').json()['provider_name'] == 'Recovered'
        assert len(calls) == 2


def test_display_uses_only_local_icon_names(monkeypatch):
    monkeypatch.setenv("OIDC_PROVIDER_NAME", "Company sign-in")
    monkeypatch.setenv("OIDC_PROVIDER_ICON", "https://untrusted.example/icon.svg")
    assert oidc_provider_display("fallback") == {"provider_name": "Company sign-in", "provider_icon": "shield"}


def test_admin_api_protects_provider_grants_but_allows_local_settings(auth_modules, manager, monkeypatch):
    manager.create_user("admin", "password123", is_admin=True)
    manager.create_user_oidc("alice", "a", "https://idp.example.com")
    monkeypatch.setenv("OIDC_ADMIN_GROUPS", "admins")
    monkeypatch.setenv("OIDC_PRIVILEGE_GROUPS", '{"can_use_bash":["operators"]}')
    app = FastAPI()
    app.include_router(auth_modules[1].setup_auth_routes(manager))
    with TestClient(app) as client:
        assert client.get('/api/auth/users').status_code == 403
        client.cookies.set('odysseus_session', manager.create_session_trusted("admin"))
        users = client.get('/api/auth/users').json()['users']
        alice = next(u for u in users if u['username'] == 'alice')
        assert alice['oidc_managed_admin'] is True
        assert alice['oidc_managed_privileges'] == ['can_use_bash']
        assert client.put('/api/auth/users/alice/admin', json={'is_admin': True}).status_code == 409
        assert client.put('/api/auth/users/alice/privileges', json={'can_use_bash': True}).status_code == 409
        assert client.put('/api/auth/users/alice/privileges', json={'can_use_research': True}).status_code == 200
        assert manager.get_privileges('alice')['can_use_research'] is True
        manager.sync_oidc_access('alice', 'a', 'https://idp.example.com', False, {'can_use_bash': False})
        assert manager.get_privileges('alice')['can_use_research'] is True


def test_oidc_only_callback_still_establishes_authenticated_session(auth_modules, manager, monkeypatch):
    monkeypatch.setenv("PASSWORD_AUTH_ENABLED", "false")
    monkeypatch.setenv("OIDC_PRIVILEGE_GROUPS", '{"can_use_bash":["operators"]}')
    claims = {'sub': 'a', 'preferred_username': 'alice', 'groups': ['operators']}
    with callback_client(auth_modules, manager, claims) as client:
        response = client.get('/api/auth/oidc/callback?code=test&state=test-state')
        assert response.status_code == 302
        token = client.cookies.get('odysseus_session')
        assert manager.get_username_for_token(token) == 'alice'
        assert manager.get_privileges('alice')['can_use_bash'] is True
        assert manager.users['alice'].get('password_hash') in ('', None)


@pytest.mark.parametrize('userinfo_sub,allowed', [('signed-subject', True), ('other-subject', False), (None, False)])
def test_signed_oidc_flow_binds_provider_permissions_to_verified_subject(auth_modules, manager, monkeypatch, userinfo_sub, allowed):
    from cryptography.fernet import Fernet
    from core import oidc
    from tests.test_oidc_manager import (
        FAKE_ISSUER, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET,
        _make_test_jwks_and_key, _make_id_token, _FakeResponse,
        _mock_discovery_response, _mock_jwks_response, _mock_token_response,
    )
    monkeypatch.setenv('OIDC_PRIVILEGE_GROUPS', '{"can_use_bash":["operators"]}')
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(oidc, '_get_state_fernet', lambda: fernet)
    jwks, _ = _make_test_jwks_and_key()
    nonce = 'synthetic-nonce'
    token = _make_id_token('signed-subject', nonce)
    userinfo = {'groups': ['operators']}
    if userinfo_sub is not None:
        userinfo['sub'] = userinfo_sub
    def get(url, **kwargs):
        if url.endswith('/.well-known/openid-configuration'):
            return _mock_discovery_response()
        if url.endswith('/jwks'):
            return _mock_jwks_response(jwks)
        if url.endswith('/userinfo'):
            return _FakeResponse(200, userinfo)
        raise AssertionError(url)
    monkeypatch.setattr(oidc.httpx, 'get', get)
    monkeypatch.setattr(oidc.httpx, 'post', lambda *args, **kwargs: _mock_token_response(token))
    provider = oidc.OidcManager(issuer=FAKE_ISSUER, client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET)
    callback = 'https://testserver/api/auth/oidc/callback'
    state = oidc._encode_state(nonce, callback, 'synthetic-pkce-verifier')
    app = FastAPI()
    app.include_router(auth_modules[2].setup_oidc_routes(manager, provider))
    with TestClient(app, base_url='https://testserver', follow_redirects=False) as client:
        client.cookies.set('odysseus_oidc_csrf', state)
        response = client.get('/api/auth/oidc/callback', params={'code':'synthetic-code', 'state':state})
        if allowed:
            assert response.headers['location'] == '/'
            username = manager.get_username_for_token(client.cookies.get('odysseus_session'))
            assert username is not None
            assert manager.get_privileges(username)['can_use_bash'] is True
        else:
            assert response.headers['location'].startswith('/login?error=')
            assert not client.cookies.get('odysseus_session')
            assert not manager.users


def test_upstream_password_reset_preserves_oidc_and_disabled_password_policy(manager, monkeypatch):
    manager.create_user('admin', 'password123', is_admin=True)
    manager.create_user('local', 'password123')
    manager.create_user_oidc('alice', 'a', 'https://idp.example.com')
    assert not manager.reset_user_password('alice', 'replacement123', 'admin')
    assert not manager.verify_password('alice', 'replacement123')
    assert manager.users['alice']['password_hash'] is None
    monkeypatch.setenv('PASSWORD_AUTH_ENABLED', 'false')
    assert not manager.reset_user_password('local', 'replacement123', 'admin')
    monkeypatch.setenv('PASSWORD_AUTH_ENABLED', 'true')
    assert manager.verify_password('local', 'password123')


def test_upstream_password_reset_revokes_other_worker_sessions(auth_modules, manager):
    manager.create_user('admin', 'password123', is_admin=True)
    manager.create_user('local', 'password123')
    other = auth_modules[0].AuthManager(manager.auth_path)
    token = other.create_session('local', 'password123')
    assert token and other.validate_token(token)
    assert manager.reset_user_password('local', 'replacement123', 'admin')
    assert not other.validate_token(token)
    assert not other.verify_password('local', 'password123')
    assert other.verify_password('local', 'replacement123')


def test_oidc_callback_under_upstream_mount_path(auth_modules, manager):
    provider = SimpleNamespace(configured=True, issuer='https://idp.example.com',
        redirect_uri_override=None, provider_name='Test',
        get_authorization_url=lambda uri: ('https://idp.example.com/authorize', 'test-state', 'nonce'),
        exchange_code=lambda *args: {'sub':'a', 'preferred_username':'alice'})
    mounted = FastAPI()
    mounted.include_router(auth_modules[2].setup_oidc_routes(manager, provider))
    app = FastAPI()
    app.mount('/odysseus', mounted)
    with TestClient(app, base_url='https://testserver', follow_redirects=False) as client:
        login = client.get('/odysseus/api/auth/oidc/login')
        assert 'Path=/odysseus/api/auth/oidc/callback' in login.headers['set-cookie']
        callback = client.get('/odysseus/api/auth/oidc/callback?code=test&state=test-state')
        assert callback.headers['location'] == '/odysseus/'
        denied = client.get('/odysseus/api/auth/oidc/callback?error=access_denied')
        assert denied.headers['location'] == '/odysseus/login?error=oidc_denied'
