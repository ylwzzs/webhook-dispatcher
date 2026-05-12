"""Webhook Dispatcher Plugin for Hermes."""
from __future__ import annotations

from webhook_dispatcher import (
    PLUGIN_DIR,
    DISPATCHER_DIR,
    CONFIG_PATH,
    HERMES_WEBHOOK,
    SUBSCRIPTIONS_PATH,
)


def register(ctx):
    ctx.register_command(
        "webhook-install",
        handler=_handle_install,
        description="引导式安装 webhook 调度中心",
    )
    ctx.register_command(
        "webhook-doctor",
        handler=_handle_doctor,
        description="诊断并修复 webhook 调度中心问题",
    )
    ctx.register_skill(f"{PLUGIN_DIR}/hermes_plugin/skill/SKILL.md")


async def _handle_install(event, ctx):
    from hermes_plugin.commands.install import run_install
    return await run_install(ctx)


async def _handle_doctor(event, ctx):
    from hermes_plugin.commands.doctor import run_doctor
    return run_doctor(ctx)
