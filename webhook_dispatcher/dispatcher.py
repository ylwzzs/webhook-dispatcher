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
from webhook_dispatcher.auth import AuthMiddleware, admin_login, admin_callback, admin_logout
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
async def admin_page(request: Request):
    """Admin page - requires OAuth authentication."""
    # Auth middleware handles authentication
    # If authenticated, user info is in request.state.user
    user = getattr(request.state, "user", None)
    return FileResponse(str(ADMIN_HTML), media_type="text/html")


@app.get("/admin/login")
async def admin_login_route(request: Request):
    """Admin login - redirect to OAuth."""
    return await admin_login(request)


@app.get("/admin/callback")
async def admin_callback_route(request: Request):
    """OAuth callback - exchange code for session."""
    return await admin_callback(request)


@app.get("/admin/logout")
async def admin_logout_route(request: Request):
    """Admin logout - clear session."""
    return await admin_logout(request)


# ---------------------------------------------------------------------------
# Alipay Authorization Callback (browser redirect)
# ---------------------------------------------------------------------------
# NOTE: This must be defined BEFORE /{route_name} to avoid being caught by it

@app.get("/api/wx-jsapi-ticket")
async def wx_jsapi_ticket(request: Request):
    """公众号 JS-SDK 签名"""
    import sys
    sys.path.insert(0, "/data/scripts")
    from wx_jsapi import sign_jsapi
    url = request.query_params.get("url", "")
    if not url:
        return {"error": "missing url"}
    try:
        return sign_jsapi(url)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/wx-oauth")
async def wx_oauth_callback(request: Request):
    """公众号网页授权回调 - 获取用户openid"""
    import sys
    sys.path.insert(0, "/data/scripts")
    from wx_oauth_route import handle_wx_oauth
    from fastapi.responses import HTMLResponse

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")

    if not code:
        return HTMLResponse(content="<h1>授权失败</h1><p>缺少code</p>", status_code=400)

    result = handle_wx_oauth(code, state)

    if result.get("success"):
        html = f"""
        <!DOCTYPE html>
        <html><head><meta charset="utf-8"><title>授权成功</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:50px;">
            <h1 style="color:#07c160;">授权成功</h1>
            <p>用户: {result.get("userid")}</p>
            <p>公众号openid: {result.get("mp_openid")}</p>
            <p>映射已保存，您可以关闭此页面。</p>
        </body></html>
        """
    else:
        html = f"<h1>授权失败</h1><p>{result.get("error")}</p>"

    return HTMLResponse(content=html)


@app.get("/alipay-auth-callback")
async def alipay_auth_callback_get(request: Request):
    """处理支付宝商家授权回调（浏览器跳转 - GET）"""
    import sys
    sys.path.insert(0, '/data/scripts')
    from alipay_auth_handler import handle_auth_callback
    
    # 获取参数
    auth_code = request.query_params.get("auth_code", "")
    app_id = request.query_params.get("app_id", "")
    state = request.query_params.get("state", "")
    
    if not auth_code:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>授权失败</h1><p>缺少 auth_code</p>", status_code=400)
    
    # 处理授权
    result = handle_auth_callback(auth_code, app_id, state)
    
    from fastapi.responses import HTMLResponse
    if result.get("success"):
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>授权成功</title></head>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h1 style="color: #52c41a;">✓ 授权成功</h1>
            <p>商家 PID: {result.get('merchant_pid')}</p>
            <p>授权令牌已保存，您可以关闭此页面。</p>
        </body>
        </html>
        """
    else:
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>授权失败</title></head>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h1 style="color: #ff4d4f;">✗ 授权失败</h1>
            <p>错误: {result.get('error', '未知错误')}</p>
        </body>
        </html>
        """
    
    return HTMLResponse(content=html)


@app.post("/alipay-auth-callback")
async def alipay_auth_callback_post(request: Request):
    """处理支付宝授权通知（POST - 异步通知）"""
    import sys
    import json
    sys.path.insert(0, '/data/scripts')
    from urllib.parse import parse_qs
    from alipay_auth_handler import ShenquanDB
    
    logger.info("[dispatcher] 收到支付宝授权通知 (POST)")
    
    # 解析 POST body
    body = await request.body()
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        params = {k: v[0] for k, v in parsed.items()}
    except Exception as e:
        logger.error(f"[dispatcher] 解析授权通知失败: {e}")
        return PlainTextResponse(content="fail", status_code=400)
    
    notify_type = params.get("notify_type", "")
    
    # 检查是否是授权通知
    if notify_type != "open_app_auth_notify":
        logger.warning(f"[dispatcher] 非授权通知: notify_type={notify_type}")
        return PlainTextResponse(content="success")
    
    status = params.get("status", "")
    if status != "execute_auth":
        logger.info(f"[dispatcher] 授权状态非执行: status={status}")
        return PlainTextResponse(content="success")
    
    # 解析 biz_content (JSON 字符串)
    biz_content_str = params.get("biz_content", "{}")
    try:
        biz_content = json.loads(biz_content_str)
    except json.JSONDecodeError:
        logger.error(f"[dispatcher] biz_content 解析失败: {biz_content_str}")
        return PlainTextResponse(content="fail", status_code=400)
    
    # 提取关键信息
    app_auth_token = biz_content.get("app_auth_token", "")
    auth_app_id = biz_content.get("auth_app_id", "")  # 商家小程序 APPID
    user_id = biz_content.get("user_id", "")  # 商家 user_id (PID)
    auth_time = biz_content.get("auth_time", "")
    expires_in = biz_content.get("expires_in", "")
    
    logger.info(f"[dispatcher] 授权通知: user_id={user_id}, auth_app_id={auth_app_id}")
    
    if not app_auth_token or not user_id:
        logger.error("[dispatcher] 缺少 app_auth_token 或 user_id")
        return PlainTextResponse(content="fail", status_code=400)
    
    # 保存到数据库
    db = ShenquanDB()
    ok = db.add_merchant_auth(user_id, app_auth_token, f"商家小程序:{auth_app_id}")
    
    if ok:
        logger.info(f"[dispatcher] 授权令牌已保存: user_id={user_id}")
        return PlainTextResponse(content="success")
    else:
        logger.error(f"[dispatcher] 保存授权令牌失败")
        return PlainTextResponse(content="fail", status_code=500)


# ---------------------------------------------------------------------------
# IoT Authorization Callback (browser redirect)
# ---------------------------------------------------------------------------

@app.get("/iot-auth-callback")
async def iot_auth_callback_get(request: Request):
    """处理 IoT 设备商家授权回调（浏览器跳转 - GET）"""
    import sys
    import importlib
    
    # 强制清理所有相关模块
    for mod in list(sys.modules.keys()):
        if 'iot' in mod.lower():
            del sys.modules[mod]
    
    sys.path.insert(0, '/data/scripts')
    
    # 重新导入
    import iot_handler
    importlib.reload(iot_handler)
    from iot_handler import handle_iot_auth_callback
    
    # 获取参数（支付宝传的是 app_auth_code）
    auth_code = request.query_params.get("auth_code", "") or request.query_params.get("app_auth_code", "")
    app_id = request.query_params.get("app_id", "")
    state = request.query_params.get("state", "")
    
    if not auth_code:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>授权失败</h1><p>缺少 auth_code</p>", status_code=400)
    
    # 处理授权
    result = handle_iot_auth_callback(auth_code, app_id, state)
    
    # 调试：打印返回值
    print(f"[DEBUG] handle_iot_auth_callback 返回: {result}")
    
    from fastapi.responses import HTMLResponse
    if result.get("success"):
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>IoT 授权成功</title></head>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h1 style="color: #52c41a;">✓ IoT 授权成功</h1>
            <p>商家 PID: {result.get('merchant_pid')}</p>
            <p>授权令牌已保存，您可以关闭此页面。</p>
        </body>
        </html>
        """
    else:
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>授权失败</title></head>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h1 style="color: #ff4d4f;">✗ 授权失败</h1>
            <p>错误: {result.get('error', '未知错误')}</p>
        </body>
        </html>
        """
    
    return HTMLResponse(content=html)


@app.post("/iot-auth-callback")
async def iot_auth_callback_post(request: Request):
    """处理 IoT 授权通知（POST - 异步通知，如商家订购）"""
    from urllib.parse import parse_qs
    import sys
    sys.path.insert(0, '/data/scripts')
    from iot_handler import IoTDB
    
    logger.info("[dispatcher] 收到 IoT 授权通知 (POST)")
    
    # 解析 POST body
    body = await request.body()
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        params = {k: v[0] for k, v in parsed.items()}
    except Exception as e:
        logger.error(f"[dispatcher] 解析 IoT 授权通知失败: {e}")
        return PlainTextResponse(content="fail", status_code=400)
    
    notify_type = params.get("notify_type", "")
    
    # 检查是否是授权通知
    if notify_type != "open_app_auth_notify":
        logger.warning(f"[dispatcher] 非授权通知: notify_type={notify_type}")
        return PlainTextResponse(content="success")
    
    status = params.get("status", "")
    if status != "execute_auth":
        logger.info(f"[dispatcher] 授权状态非执行: status={status}")
        return PlainTextResponse(content="success")
    
    # 解析 biz_content (JSON 字符串)
    biz_content_str = params.get("biz_content", "{}")
    try:
        biz_content = json.loads(biz_content_str)
    except json.JSONDecodeError:
        logger.error(f"[dispatcher] biz_content 解析失败: {biz_content_str}")
        return PlainTextResponse(content="fail", status_code=400)
    
    # 提取关键信息
    app_auth_token = biz_content.get("app_auth_token", "")
    auth_app_id = biz_content.get("auth_app_id", "")  # 商家小程序 APPID
    user_id = biz_content.get("user_id", "")  # 商家 user_id (PID)
    auth_time = biz_content.get("auth_time", "")
    expires_in = biz_content.get("expires_in", "")
    
    logger.info(f"[dispatcher] IoT 授权通知: user_id={user_id}, auth_app_id={auth_app_id}")
    
    if not app_auth_token or not user_id:
        logger.error("[dispatcher] 缺少 app_auth_token 或 user_id")
        return PlainTextResponse(content="fail", status_code=400)
    
    # 保存到数据库
    db = IoTDB()
    ok = db.add_merchant_auth(user_id, app_auth_token, f"IoT商家:{auth_app_id}")
    
    if ok:
        logger.info(f"[dispatcher] IoT 授权令牌已保存: user_id={user_id}")
        return PlainTextResponse(content="success")
    else:
        logger.error("[dispatcher] 保存 IoT 授权令牌失败")
        return PlainTextResponse(content="fail", status_code=500)


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
            # 详细日志：记录完整 payload（审批回调用）
            if payload.get('Event') == 'sys_approval_change':
                logger.info(f"[dispatcher] 审批回调完整数据:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
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
