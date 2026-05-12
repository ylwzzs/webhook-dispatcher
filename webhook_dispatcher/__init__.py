"""Webhook Dispatcher — decrypt, match rules, forward callbacks to Hermes AI."""
from __future__ import annotations

import os
from pathlib import Path

__version__ = "2.0.0"

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
PLUGIN_DIR = HERMES_HOME / "plugins/webhook-dispatcher"
DISPATCHER_DIR = PLUGIN_DIR / "webhook_dispatcher"
CONFIG_PATH = DISPATCHER_DIR / "config.yaml"
HERMES_WEBHOOK = HERMES_HOME / "hermes-agent/gateway/platforms/webhook.py"
SUBSCRIPTIONS_PATH = HERMES_HOME / "webhook_subscriptions.json"
