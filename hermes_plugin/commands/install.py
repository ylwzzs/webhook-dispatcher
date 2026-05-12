#!/usr/bin/env python3
"""/webhook-install — Guided installation for webhook dispatcher."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
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
NGINX_CONF = Path(os.environ.get("NGINX_CONF", "/etc/nginx/sites-available/default"))
SERVICE_PATH = Path.home() / ".config/systemd/user/webhook-dispatcher.service"


def _run(cmd: str) -> tuple[bool, str]:
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True).strip()
        return True, out
    except subprocess.CalledProcessError as e:
        return False, (e.output or "").strip()


def _step(n: int, total: int, msg: str):
    print(f"\nStep {n}/{total}: {msg}")


def _ok(msg: str):
    print(f"  ✓ {msg}")


def _fail(msg: str):
    print(f"  ✗ {msg}")


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val or default


def _ask_secret(prompt: str, env_var: str = "", default: str = "") -> str:
    """Ask for a secret, falling back to env var then default. Never echo defaults."""
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val:
            _ok(f"{prompt} (from ${env_var})")
            return env_val
    val = _ask(prompt, default="<interactive>")
    if val == "<interactive>":
        return default
    return val


def _generate_secret(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


async def run_install(ctx=None):
    print("\U0001f527 Webhook 调度中心安装向导\n")

    # Step 1: Dependencies
    _step(1, 5, "检查依赖")
    ok, _ = _run("python3 --version")
    python_ok = ok
    _ok("Python " + (_.split()[-1] if ok else "not found")) if ok else _fail("Python not found")

    ok, _ = _run("nginx -v")
    _ok("nginx") if ok else _fail("nginx not found")

    ok, _ = _run("systemctl --user is-active hermes-gateway.service")
    _ok("Hermes gateway running") if ok and "active" in _ else _fail("Hermes gateway not running")

    if not python_ok:
        print("\n❌ 缺少必要依赖，请先安装 Python 3.12+")
        return

    # Step 2: Configure decryptors
    _step(2, 5, "配置解密方式")
    print("  你需要接收哪些平台的回调？")
    print("  [1] 企微应用回调 (WeCom AES)")
    print("  [2] 支付宝回调 (Alipay RSA2)")
    print("  [3] 通用 Webhook (无加密)")
    choices = _ask("选择（多选用逗号分隔）", "1")

    import yaml

    credentials = {}
    routes = {}
    selected = [c.strip() for c in choices.split(",")]

    # Shared secret for Hermes webhook callback verification
    default_secret = _ask_secret("Hermes 回调验证 Secret", env_var="WEBHOOK_DEFAULT_SECRET")
    if not default_secret:
        default_secret = _generate_secret()
        _ok(f"已生成随机 secret: {default_secret[:8]}...")

    if "1" in selected:
        print("\n  企微应用配置:")
        token = _ask("Token")
        aes_key = _ask("EncodingAESKey")
        corp_id = _ask("CorpID")
        agent_id = _ask("AgentID")
        corp_secret = _ask_secret("CorpSecret", env_var="WECOM_CORP_SECRET")
        route_name = _ask("路由名称", "wecom")

        credentials[f"{route_name}-default"] = {
            "type": "wecom_aes",
            "token": token,
            "encoding_aes_key": aes_key,
            "receive_id": corp_id,
        }
        routes[route_name] = {
            "description": "企微应用回调",
            "credentials": f"{route_name}-default",
            "secret": default_secret,
            "decrypt_optional": True,
            "forward_to": route_name,
            "rules": [
                {"name": "ignore-subscribe", "enabled": True, "conditions": {"Event": "subscribe"}, "action": {"type": "ignore"}},
                {"name": "ignore-text", "enabled": True, "conditions": {"MsgType": "text"}, "action": {"type": "ignore"}},
                {"name": "ignore-enter-agent", "enabled": True, "conditions": {"Event": "enter_agent"}, "action": {"type": "ignore"}},
                {"name": "default-forward", "enabled": True, "conditions": {}, "action": {"type": "forward"},
                 "context": "你是企微应用助手，收到回调事件时简要描述事件内容",
                 "prompt": "处理此回调事件"},
            ],
        }

    if "2" in selected:
        print("\n  支付宝配置:")
        alipay_key = _ask_secret("AlipayPublicKey", env_var="ALIPAY_PUBLIC_KEY")
        route_name = _ask("路由名称", "alipay")

        credentials[f"{route_name}-default"] = {
            "type": "alipay_rsa",
            "alipay_public_key": alipay_key,
            "charset": "UTF-8",
            "sign_type": "RSA2",
        }
        routes[route_name] = {
            "description": "支付宝回调通知",
            "credentials": f"{route_name}-default",
            "forward_to": route_name,
            "rules": [
                {"name": "default-forward", "enabled": True, "conditions": {}, "action": {"type": "forward"},
                 "context": "收到支付宝回调通知，简要描述内容"},
            ],
        }

    if "3" in selected:
        route_name = _ask("路由名称", "custom")
        routes[route_name] = {
            "description": "通用 Webhook",
            "forward_to": route_name,
            "rules": [
                {"name": "default-forward", "enabled": True, "conditions": {}, "action": {"type": "forward"}},
            ],
        }

    # Step 3: Configure forward target
    _step(3, 5, "配置转发目标")
    target_platform = _ask("事件转发到哪个平台", "wecom")
    target_chat_id = _ask("ChatID")
    if not target_chat_id:
        _fail("ChatID 不能为空")
        return

    # Server port and auth
    server_port = int(_ask("Dispatcher 端口", "8646"))
    shared_secret = _ask_secret("Dispatcher 鉴权 Secret", env_var="DISPATCHER_SHARED_SECRET")
    if not shared_secret:
        shared_secret = _generate_secret()
        _ok(f"已生成随机鉴权 secret: {shared_secret[:8]}...")

    # Domain for callback URLs
    domain = _ask("外部访问域名", os.environ.get("WEBHOOK_DOMAIN", ""))

    # Step 4: Install
    _step(4, 5, "安装服务")

    # Write dispatcher config
    config = {
        "server": {
            "host": "0.0.0.0",
            "port": server_port,
            "auth": {
                "mode": "shared_secret",
                "shared_secret": shared_secret,
            },
        },
        "hermes": {
            "webhook_url": f"http://127.0.0.1:8644/webhooks/{{route}}",
            "default_secret": default_secret,
        },
        "forward": {
            "max_retries": 2,
            "retry_delay": 1,
        },
        "credentials": credentials,
        "routes": routes,
    }
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    _ok("写入 dispatcher 配置")

    # Create venv and install deps
    if not (DISPATCHER_DIR / "venv/bin/python").exists():
        ok, _ = _run(f"cd {DISPATCHER_DIR} && python3 -m venv venv")
        if ok:
            _ok("创建虚拟环境")
        else:
            _fail("创建虚拟环境失败")
    ok, _ = _run(f"{DISPATCHER_DIR}/venv/bin/pip install -q fastapi uvicorn httpx cryptography pyyaml")
    _ok("安装 Python 依赖") if ok else _fail("安装依赖失败")

    # Systemd service — use template file if available, otherwise generate inline
    systemd_template = PLUGIN_DIR / "systemd" / "webhook-dispatcher.service"
    SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if systemd_template.exists():
        service_content = systemd_template.read_text()
        service_content = service_content.replace("{{DISPATCHER_DIR}}", str(DISPATCHER_DIR))
        service_content = service_content.replace("{{PORT}}", str(server_port))
        with open(SERVICE_PATH, "w") as f:
            f.write(service_content)
        _ok(f"配置 systemd 服务 (从模板)")
    else:
        with open(SERVICE_PATH, "w") as f:
            f.write(f"""[Unit]
Description=Webhook Dispatcher - Decrypt, Rule-match, Forward to Hermes
After=network.target

[Service]
Type=simple
WorkingDirectory={DISPATCHER_DIR}
ExecStart={DISPATCHER_DIR}/venv/bin/uvicorn dispatcher:app --host 0.0.0.0 --port {server_port}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""")
        _ok("配置 systemd 服务")

    _run("systemctl --user daemon-reload")

    # Patch Hermes webhook
    ok, out = _run(f"python3 {PLUGIN_DIR}/patch_webhook_inject.py")
    _ok("Patch Hermes webhook (inject_to)") if ok else _fail("Patch 失败: " + out)

    # Update webhook_subscriptions.json
    subs = {}
    for route_name in routes:
        subs[route_name] = {
            "description": routes[route_name].get("description", ""),
            "events": [],
            "secret": routes[route_name].get("secret", "INSECURE_NO_AUTH"),
            "deliver": target_platform,
            "deliver_extra": {"chat_id": target_chat_id},
            "inject_to": {
                "platform": target_platform,
                "chat_id": target_chat_id,
                "user_id": target_chat_id,
            },
            "prompt": "{_rule_prompt}\n\n上下文约定：{_rule_context}\n\n事件数据：{__raw__}",
        }
    with open(SUBSCRIPTIONS_PATH, "w") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)
    _ok("更新 webhook_subscriptions.json")

    # Disable wecom_callback adapter
    try:
        with open(HERMES_CONFIG) as f:
            cfg = yaml.safe_load(f)
        if "platforms" in cfg and "wecom_callback" in cfg["platforms"]:
            cfg["platforms"]["wecom_callback"]["enabled"] = False
            with open(HERMES_CONFIG, "w") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            _ok("禁用 wecom_callback adapter")
    except Exception:
        _fail("禁用 wecom_callback 失败")

    # Start services
    _run("systemctl --user restart webhook-dispatcher.service")
    _ok("启动 dispatcher 服务")
    _run("systemctl --user restart hermes-gateway.service")
    _ok("重启 Hermes gateway")

    # Step 5: Verify
    _step(5, 5, "验证")

    import time
    time.sleep(3)

    ok, _ = _run("systemctl --user is-active webhook-dispatcher.service")
    _ok("dispatcher 服务运行中") if ok and "active" in _ else _fail("dispatcher 未运行")

    ok, _ = _run("systemctl --user is-active hermes-gateway.service")
    _ok("Hermes gateway 运行中") if ok and "active" in _ else _fail("Hermes gateway 未运行")

    ok, _ = _run(f"curl -sf http://127.0.0.1:{server_port}/health")
    _ok("dispatcher 健康检查通过") if ok and "ok" in _ else _fail("dispatcher 健康检查失败")

    try:
        with open(HERMES_WEBHOOK) as f:
            _ok("inject_to patch 已生效") if "inject_to" in f.read() else _fail("inject_to patch 未生效")
    except FileNotFoundError:
        _fail("hermes webhook.py 未找到")

    print("\n\U0001f389 安装完成！")
    for route_name in routes:
        if domain:
            print(f"  回调 URL: https://{domain}/{route_name}")
        else:
            print(f"  回调 URL: http://127.0.0.1:{server_port}/{route_name}")
    print(f"\n  后续可通过 /webhook-doctor 检查健康状态")
    print(f"  在对话中告诉 AI \"添加一条 webhook 规则\" 即可配置")
