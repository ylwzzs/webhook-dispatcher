"""Webhook Dispatcher — FastAPI service that decrypts, matches rules, and forwards to Hermes."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse

from webhook_dispatcher.admin import router as admin_router
from webhook_dispatcher.auth import AuthMiddleware
from webhook_dispatcher.config import ConfigManager
from webhook_dispatcher.eventlog import EventLog
from webhook_dispatcher.forwarder import Forwarder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dispatcher")

# ---------------------------------------------------------------------------
# Initialize components
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.yaml"
ADMIN_HTML = Path(__file__).parent / "admin.html"

config_mgr = ConfigManager(str(CONFIG_PATH))
event_log = EventLog()
forwarder = Forwarder(config_mgr, event_log)

app = FastAPI(title="Webhook Dispatcher", version="2.0.0")
app.add_middleware(AuthMiddleware, config_manager=config_mgr)

# Expose components to admin routes
app.state.config_mgr = config_mgr
app.state.event_log = event_log

# Register admin API
app.include_router(admin_router)


@app.on_event("startup")
async def startup():
    await forwarder.start()
    logger.info("[dispatcher] 服务启动完成")


@app.on_event("shutdown")
async def shutdown():
    await forwarder.stop()
    logger.info("[dispatcher] 服务关闭")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event_type(payload: dict) -> str:
    return payload.get("Event") or payload.get("MsgType") or payload.get("event_type") or "unknown"


def _parse_body(body: bytes) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    try:
        from urllib.parse import parse_qs
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    config_mgr.maybe_reload()
    return {
        "status": "ok",
        "service": "webhook-dispatcher",
        "version": "2.0.0",
        "routes": list(config_mgr.routes.keys()),
        "stats": event_log.stats,
    }


@app.get("/admin")
async def admin_page():
    return FileResponse(str(ADMIN_HTML), media_type="text/html")


@app.api_route("/{route_name}", methods=["GET", "POST"])
async def handle_route(route_name: str, request: Request):
    config_mgr.maybe_reload()

    route_config = config_mgr.get_route(route_name)
    if not route_config:
        return Response(content=f"Unknown route: {route_name}", status_code=404)

    # GET: verification
    if request.method == "GET":
        dec = config_mgr.get_decryptor(route_name)
        if dec:
            query = dict(request.query_params)
            result = dec.verify(query)
            if result is not None:
                return PlainTextResponse(content=result)
        return Response(content="verification failed", status_code=403)

    # POST: callback processing
    t0 = time.monotonic()
    body = await request.body()
    query = dict(request.query_params)

    # 1. Decrypt
    payload: Optional[Dict[str, Any]] = None
    dec = config_mgr.get_decryptor(route_name)
    if dec:
        payload = dec.decrypt(body, query)
        if payload:
            logger.info(f"[dispatcher] 解密成功 route={route_name} MsgType={payload.get('MsgType')} Event={payload.get('Event')}")
        elif route_config.get("decrypt_optional"):
            logger.info(f"[dispatcher] 解密失败，尝试明文解析 route={route_name}")
        else:
            logger.warning(f"[dispatcher] 解密失败 route={route_name}")
            return Response(content="decrypt failed", status_code=400)

    # 2. Parse as JSON/form
    if payload is None:
        payload = _parse_body(body)
        if payload is None:
            return Response(content="cannot parse body", status_code=400)

    evt_type = _event_type(payload)

    # 3. Rule engine
    engine = config_mgr.get_rule_engine(route_name)
    matched = engine.match(payload) if engine else None

    if matched:
        action = matched.get("action", {})
        action_type = action.get("type", "")
        rule_name = matched.get("name", "unknown")

        if action_type == "ignore":
            logger.info(f"[dispatcher] 规则 '{rule_name}' 忽略事件")
            duration_ms = int((time.monotonic() - t0) * 1000)
            event_log.log_event(route_name, evt_type, rule_name, "ignore", True, duration_ms)
            return PlainTextResponse(content="success")

        elif action_type in ("forward", "ai_process"):
            payload = Forwarder.apply_context(payload, matched)
            target = action.get("target") or route_config.get("forward_to") or route_name
            ok = await forwarder.forward(route_name, payload, target)
            duration_ms = int((time.monotonic() - t0) * 1000)
            event_log.log_event(route_name, evt_type, rule_name, action_type, ok, duration_ms)
            return PlainTextResponse(
                content="success" if ok else "forward failed",
                status_code=200 if ok else 502,
            )

        elif action_type == "send_message":
            logger.info(f"[dispatcher] 规则 '{rule_name}' send_message (not implemented)")
            duration_ms = int((time.monotonic() - t0) * 1000)
            event_log.log_event(route_name, evt_type, rule_name, "send_message", True, duration_ms)
            return PlainTextResponse(content="not implemented", status_code=501)

    # 4. No rule matched: default forward
    forward_to = route_config.get("forward_to") or route_name
    ok = await forwarder.forward(route_name, payload, forward_to)
    duration_ms = int((time.monotonic() - t0) * 1000)
    event_log.log_event(route_name, evt_type, "default", "forward", ok, duration_ms)
    return PlainTextResponse(
        content="success" if ok else "forward failed",
        status_code=200 if ok else 502,
    )
