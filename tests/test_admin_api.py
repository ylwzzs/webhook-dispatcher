"""Tests for the admin API."""
import pytest
import yaml
from pathlib import Path
from fastapi.testclient import TestClient

from webhook_dispatcher.config import ConfigManager
from webhook_dispatcher.eventlog import EventLog
from webhook_dispatcher.dispatcher import app


@pytest.fixture
def client(tmp_path):
    """Create a test client with a temp config."""
    config = {
        "server": {"host": "0.0.0.0", "port": 8646, "auth": {"mode": "none"}},
        "hermes": {
            "webhook_url": "http://127.0.0.1:8644/webhooks/{route}",
            "default_secret": "test",
        },
        "forward": {"max_retries": 2, "retry_delay": 1},
        "credentials": {},
        "routes": {
            "test": {
                "description": "test",
                "forward_to": "test",
                "rules": [
                    {"name": "default", "enabled": True, "conditions": {}, "action": {"type": "forward"}},
                ],
            },
        },
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    mgr = ConfigManager(str(config_path))
    elog = EventLog(str(tmp_path / "logs"))
    app.state.config_mgr = mgr
    app.state.event_log = elog

    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_list_routes(client):
    resp = client.get("/api/routes")
    assert resp.status_code == 200
    assert any(r["name"] == "test" for r in resp.json())


def test_create_credential(client):
    resp = client.post("/api/credentials", json={
        "name": "wecom-test",
        "type": "wecom_aes",
        "config": {"token": "abc", "encoding_aes_key": "a" * 43, "receive_id": "corp123"},
    })
    assert resp.status_code == 200
    assert resp.json()["ok"]


def test_add_rule_conflict_detection(client):
    # Add a rule that conflicts with existing default (empty conditions)
    resp = client.post("/api/routes/test/rules", json={
        "name": "conflict-rule",
        "enabled": True,
        "conditions": {},
        "action": {"type": "ignore"},
        "context": "",
        "prompt": "",
    })
    data = resp.json()
    assert data["ok"] is False
    assert len(data["conflicts"]) > 0


def test_stats(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json()["routes"] >= 1
