"""Tests for the config manager."""
import pytest
import yaml
from pathlib import Path
from webhook_dispatcher.config import ConfigManager


@pytest.fixture
def config_dir(tmp_path):
    """Create a minimal config.yaml in a temp directory."""
    config = {
        "server": {"host": "0.0.0.0", "port": 8646, "auth": {"mode": "none"}},
        "hermes": {
            "webhook_url": "http://127.0.0.1:8644/webhooks/{route}",
            "default_secret": "test-secret",
        },
        "forward": {"max_retries": 2, "retry_delay": 1},
        "credentials": {},
        "routes": {
            "test": {
                "description": "test route",
                "forward_to": "test",
                "rules": [
                    {"name": "default", "enabled": True, "conditions": {}, "action": {"type": "forward"}},
                ],
            },
        },
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return tmp_path


def test_load_config(config_dir):
    mgr = ConfigManager(str(config_dir / "config.yaml"))
    assert "test" in mgr.routes
    assert mgr.hermes_secret == "test-secret"
    assert mgr.max_retries == 2


def test_hot_reload(config_dir):
    mgr = ConfigManager(str(config_dir / "config.yaml"))
    assert "test" in mgr.routes

    # Add a new route
    config_path = config_dir / "config.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    cfg["routes"]["test2"] = {"description": "test2", "forward_to": "test2", "rules": []}
    with open(config_path, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    mgr.maybe_reload()
    assert "test2" in mgr.routes


def test_save_config(config_dir):
    mgr = ConfigManager(str(config_dir / "config.yaml"))
    mgr._routes["new-route"] = {"description": "new", "forward_to": "new", "rules": []}
    mgr.save()

    # Force reload
    mgr._last_mtime = 0.0
    mgr.maybe_reload()
    assert "new-route" in mgr.routes
