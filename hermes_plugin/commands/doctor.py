#!/usr/bin/env python3
"""Webhook Dispatcher doctor — diagnose and fix issues."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from webhook_dispatcher import (
    PLUGIN_DIR,
    DISPATCHER_DIR,
    CONFIG_PATH,
    HERMES_WEBHOOK,
    SUBSCRIPTIONS_PATH,
)

HERMES_HOME = PLUGIN_DIR.parent.parent
HERMES_CONFIG = HERMES_HOME / "config.yaml"
NGINX_CONF = os.environ.get("NGINX_CONF", "/etc/nginx/sites-available/default")


def _run(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True).strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip() if e.output else ""


def _check(label: str, ok: bool, detail: str = "", fix: str = ""):
    icon = "✓" if ok else "✗"
    line = f"[{icon}] {label}"
    if detail:
        line += f"  — {detail}"
    if not ok and fix:
        line += f"\n    修复: {fix}"
    print(line)
    return ok


def _get_dispatcher_port() -> int:
    """Read the configured port from config.yaml, fallback to 8646."""
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        return int(cfg.get("server", {}).get("port", 8646))
    except Exception:
        return 8646


def run_doctor(ctx=None) -> list:
    """Run all diagnostic checks. Returns list of issues."""
    issues = []
    port = _get_dispatcher_port()
    print("\U0001f3e5 Webhook 调度中心诊断\n")

    # 1. Dispatcher service
    status = _run("systemctl --user is-active webhook-dispatcher.service")
    pid = _run("systemctl --user show webhook-dispatcher --property=MainPID --value")
    ok = status == "active"
    _check("dispatcher 服务", ok,
           f"running (pid {pid})" if ok else "not running",
           "systemctl --user start webhook-dispatcher")
    if not ok:
        issues.append("dispatcher not running")

    # 2. Dispatcher health
    health = _run(f"curl -sf http://127.0.0.1:{port}/health")
    ok = "ok" in health
    _check("dispatcher 健康检查", ok,
           "responding" if ok else "no response",
           "systemctl --user restart webhook-dispatcher")
    if not ok:
        issues.append("dispatcher unhealthy")

    # 3. Hermes gateway
    gw_status = _run("systemctl --user is-active hermes-gateway.service")
    ok = gw_status == "active"
    _check("Hermes gateway", ok,
           "running" if ok else "not running",
           "systemctl --user start hermes-gateway")
    if not ok:
        issues.append("hermes not running")

    # 4. inject_to patch
    try:
        with open(HERMES_WEBHOOK) as f:
            patched = "inject_to" in f.read()
    except FileNotFoundError:
        patched = False
    _check("inject_to patch", patched,
           "applied" if patched else "未生效（Hermes 升级后丢失？）",
           f"python3 {PLUGIN_DIR}/patch_webhook_inject.py && systemctl --user restart hermes-gateway")
    if not patched:
        issues.append("inject_to patch missing")

    # 5. webhook_subscriptions inject_to config
    try:
        with open(SUBSCRIPTIONS_PATH) as f:
            subs = json.load(f)
        has_inject = any("inject_to" in v for v in subs.values() if isinstance(v, dict))
    except Exception:
        has_inject = False
    _check("webhook_subscriptions", has_inject,
           "inject_to configured" if has_inject else "missing inject_to",
           f"检查 {SUBSCRIPTIONS_PATH} 中路由的 inject_to 配置")
    if not has_inject:
        issues.append("subscriptions missing inject_to")

    # 6. Nginx proxy
    try:
        with open(NGINX_CONF) as f:
            nginx_conf = f.read()
        has_port = str(port) in nginx_conf
        has_auth_header = "X-Dispatcher-Secret" in nginx_conf
    except Exception:
        has_port = False
        has_auth_header = False
    _check("Nginx 代理", has_port,
           f"指向 dispatcher:{port}" if has_port else f"未配置到 :{port}",
           f"更新 nginx 配置指向 127.0.0.1:{port}")
    if not has_port:
        issues.append("nginx not proxied to dispatcher")

    # 7. Nginx auth header
    _check("Nginx 鉴权 header", has_auth_header,
           "X-Dispatcher-Secret 已配置" if has_auth_header else "未配置 X-Dispatcher-Secret",
           "在 nginx location 中添加 proxy_set_header X-Dispatcher-Secret")
    if not has_auth_header:
        issues.append("nginx missing auth header")

    # 8. wecom_callback disabled
    try:
        import yaml
        with open(HERMES_CONFIG) as f:
            cfg = yaml.safe_load(f)
        wecom_cb = cfg.get("platforms", {}).get("wecom_callback", {})
        cb_disabled = not wecom_cb.get("enabled", False)
    except Exception:
        cb_disabled = True
    _check("wecom_callback adapter", cb_disabled,
           "disabled (正确，由 dispatcher 接管)" if cb_disabled else "enabled (应由 dispatcher 接管)",
           "在 config.yaml 中设置 wecom_callback.enabled: false")
    if not cb_disabled:
        issues.append("wecom_callback should be disabled")

    # 9. Decryptors loaded
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        routes = cfg.get("routes", {})
        cred_count = len(cfg.get("credentials", {}))
        dec_count = sum(1 for r in routes.values() if r.get("credentials"))
    except Exception:
        cred_count = 0
        dec_count = 0
    _check("凭据+解密器", dec_count > 0,
           f"{cred_count} 凭据, {dec_count} 路由已关联" if dec_count else "未配置",
           "在 dispatcher/config.yaml 中配置 credentials 和 routes")
    if dec_count == 0:
        issues.append("no decryptors configured")

    # 10. Rules loaded
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        routes = cfg.get("routes", {})
        rule_count = sum(len(r.get("rules", [])) for r in routes.values())
    except Exception:
        rule_count = 0
    _check("规则引擎", rule_count > 0,
           f"{rule_count} 条规则" if rule_count else "0 条规则（建议添加）",
           "在 dispatcher/config.yaml 中添加 rules")
    if rule_count == 0:
        issues.append("no rules configured")

    # 11. Auth mode
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        auth_cfg = cfg.get("server", {}).get("auth", {})
        auth_mode = auth_cfg.get("mode", "none")
    except Exception:
        auth_mode = "none"
    _check("鉴权配置", auth_mode != "none",
           f"mode={auth_mode}" if auth_mode != "none" else "未启用（建议配置 shared_secret 或 ip_whitelist）",
           "在 config.yaml 的 server.auth 中配置鉴权")
    if auth_mode == "none":
        issues.append("auth not configured")

    # 12. Event log
    log_dir = PLUGIN_DIR / "logs"
    log_files = list(log_dir.glob("events-*.jsonl")) if log_dir.exists() else []
    _check("事件日志", True,
           f"{len(log_files)} 个日志文件" if log_files else "目录已就绪（首次事件后生成）")

    # 13. Forward config
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        fwd = cfg.get("forward", {})
        max_retries = fwd.get("max_retries", "not set")
        _check("转发重试", bool(fwd),
               f"max_retries={max_retries}" if fwd else "未配置（使用默认值）")
    except Exception:
        _check("转发重试", False, "无法读取配置")

    # Summary
    if issues:
        print(f"\n⚠ 发现 {len(issues)} 个问题")
    else:
        print("\n✓ 所有检查通过！")

    return issues
