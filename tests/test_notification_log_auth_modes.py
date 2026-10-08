"""Persisting editor toasts must not trigger a login reload in no-login mode."""
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from core.database import Base
    from routes.task import task_routes
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(task_routes, "SessionLocal", sessionmaker(bind=engine))
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(task_routes.setup_task_routes(Mock()))
    with TestClient(app) as connection:
        yield connection
    engine.dispose()


def test_toasts_round_trip_in_explicit_no_login_mode(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert client.post("/api/tasks/notification-logs", json={"body": "Saved synthetic draft"}).status_code == 200
    response = client.get("/api/tasks/notification-logs")
    assert response.status_code == 200
    assert [n["body"] for n in response.json()["notifications"]] == ["Saved synthetic draft"]


def test_anonymous_cannot_read_or_write_logs_when_auth_enabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    assert client.post("/api/tasks/notification-logs", json={"body": "Untrusted"}).status_code == 401
    assert client.get("/api/tasks/notification-logs").status_code == 401
