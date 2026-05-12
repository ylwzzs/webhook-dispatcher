#!/usr/bin/env python3
"""Patch Hermes webhook.py with inject_to support.

Usage:
    python3 patch_webhook_inject.py          # Apply patch
    python3 patch_webhook_inject.py --check  # Only check, don't modify

Idempotent: safe to run multiple times.
Run after Hermes upgrades to restore inject_to support.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from webhook_dispatcher import HERMES_WEBHOOK, PLUGIN_DIR

filepath = str(HERMES_WEBHOOK)
patched_hash_file = str(PLUGIN_DIR / ".patched_hash")
check_only = "--check" in sys.argv


def get_file_hash(path: str) -> str:
    try:
        content = Path(path).read_bytes()
        return hashlib.sha256(content).hexdigest()[:12]
    except FileNotFoundError:
        return ""


def is_patched() -> bool:
    try:
        content = Path(filepath).read_text()
        return "inject_to" in content
    except FileNotFoundError:
        return False


def save_patched_hash(h: str) -> None:
    Path(patched_hash_file).write_text(h)


def get_saved_hash() -> str:
    try:
        return Path(patched_hash_file).read_text().strip()
    except FileNotFoundError:
        return ""


# Check if already patched
if is_patched():
    current_hash = get_file_hash(filepath)
    save_patched_hash(current_hash)
    print(f"✓ inject_to patch already applied. (hash: {current_hash})")
    sys.exit(0)

if check_only:
    print("✗ inject_to patch NOT applied.")
    sys.exit(1)

# Check if Hermes was upgraded since last patch
saved_hash = get_saved_hash()
current_hash = get_file_hash(filepath)
if saved_hash and current_hash and saved_hash != current_hash:
    print(f"⚠ Hermes webhook.py has changed (was {saved_hash}, now {current_hash})")
    print("  This likely means Hermes was upgraded. Attempting re-patch...")

with open(filepath, "r") as f:
    content = f.read()

old = '''        # Use delivery_id in session key so concurrent webhooks on the
        # same route get independent agent runs (not queued/interrupted).
        session_chat_id = f"webhook:{route_name}:{delivery_id}"

        # Store delivery info for send().  Read by every send() invocation
        # for this chat_id (interim status messages and the final response),
        # so we do NOT pop on send.  TTL-based cleanup keeps the dict bounded.
        deliver_config = {
            "deliver": route_config.get("deliver", "log"),
            "deliver_extra": self._render_delivery_extra(
                route_config.get("deliver_extra", {}), payload
            ),
            "payload": payload,
        }
        self._delivery_info[session_chat_id] = deliver_config
        self._delivery_info_created[session_chat_id] = now
        self._prune_delivery_info(now)

        # Build source and event
        source = self.build_source(
            chat_id=session_chat_id,
            chat_name=f"webhook/{route_name}",
            chat_type="webhook",
            user_id=f"webhook:{route_name}",
            user_name=route_name,
        )'''

new = '''        # inject_to: inject event into an existing platform session
        # so AI has the full conversation context and replies via that platform.
        inject_to = route_config.get("inject_to")
        if inject_to:
            inject_platform = inject_to.get("platform", "wecom")
            inject_chat_id = inject_to.get("chat_id", "")
            inject_user_id = inject_to.get("user_id", inject_chat_id)
            session_chat_id = inject_chat_id
            source = self.build_source(
                chat_id=inject_chat_id,
                chat_name=inject_user_id,
                chat_type="dm",
                user_id=inject_user_id,
                user_name=inject_user_id,
            )
            try:
                source.platform = Platform(inject_platform)
            except ValueError:
                pass
        else:
            session_chat_id = f"webhook:{route_name}:{delivery_id}"
            source = self.build_source(
                chat_id=session_chat_id,
                chat_name=f"webhook/{route_name}",
                chat_type="webhook",
                user_id=f"webhook:{route_name}",
                user_name=route_name,
            )

        # Store delivery info for send()
        deliver_config = {
            "deliver": route_config.get("deliver", "log"),
            "deliver_extra": self._render_delivery_extra(
                route_config.get("deliver_extra", {}), payload
            ),
            "payload": payload,
        }
        self._delivery_info[session_chat_id] = deliver_config
        self._delivery_info_created[session_chat_id] = now
        self._prune_delivery_info(now)'''

if old not in content:
    print("ERROR: Cannot find target code block in webhook.py.")
    print("Hermes may have updated the file structure.")
    print("Manual patch needed — search for 'session_chat_id = f\"webhook:{route_name}'")
    print(f"File: {filepath}")
    print(f"Current hash: {current_hash}")
    sys.exit(1)

content = content.replace(old, new)

with open(filepath, "w") as f:
    f.write(content)

# Save the hash of the patched file
new_hash = get_file_hash(filepath)
save_patched_hash(new_hash)

print("✓ webhook.py patched with inject_to support.")
print(f"  Hash: {new_hash}")
print("  Restart Hermes: systemctl --user restart hermes-gateway.service")
