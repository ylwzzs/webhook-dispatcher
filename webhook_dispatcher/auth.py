"""Internal auth middleware for the dispatcher service.

Supports:
1. Admin API: WeCom OAuth login
2. Business callbacks: shared_secret verification
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode, quote
import json

import requests
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WeCom OAuth Configuration
# ---------------------------------------------------------------------------

CORPID = "ww44d4d18ae26a6620"
AGENTID = 1000078
SECRET = "JBv0GYrjt4q9xTm_MUFjafY0DgLHPdIgJXilzbq8ynU"

# Allowed users for admin access
ALLOWED_USERS = ["ZhangDuo"]

# Session configuration
SESSION_FILE = Path("/data/config/admin_sessions.json")
SESSION_EXPIRE_SECONDS = 3600 * 8  # 8 hours

# Token cache
TOKEN_CACHE_FILE = Path("/data/config/wecom_token_cache.json")


# ---------------------------------------------------------------------------
# WeCom OAuth Helper Functions
# ---------------------------------------------------------------------------

def _get_access_token() -> Optional[str]:
    """Get WeCom access token."""
    if TOKEN_CACHE_FILE.exists():
        try:
            with open(TOKEN_CACHE_FILE) as f:
                data = json.load(f)
                if data.get("expires_at", 0) > time.time():
                    return data["access_token"]
        except Exception:
            pass
    
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORPID}&corpsecret={SECRET}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            with open(TOKEN_CACHE_FILE, "w") as f:
                json.dump({
                    "access_token": token,
                    "expires_at": time.time() + expires_in - 60
                }, f)
            return token
    except Exception as e:
        logger.error(f"[oauth] 获取 access_token 失败: {e}")
    return None


def _get_user_info(code: str) -> Optional[dict]:
    """Get user info from OAuth code."""
    token = _get_access_token()
    if not token:
        return None
    
    url = f"https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo?access_token={token}&code={code}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            user_id = data.get("UserId")
            if user_id:
                detail_url = f"https://qyapi.weixin.qq.com/cgi-bin/user/get?access_token={token}&userid={user_id}"
                detail_resp = requests.get(detail_url, timeout=10)
                detail_data = detail_resp.json()
                return {
                    "user_id": user_id,
                    "name": detail_data.get("name", user_id),
                    "avatar": detail_data.get("avatar", ""),
                }
    except Exception as e:
        logger.error(f"[oauth] 获取用户信息异常: {e}")
    return None


def _build_oauth_url(redirect_uri: str, state: str = "") -> str:
    """Build WeCom QR scan login URL (external - scan QR code)."""
    # 企业自建应用扫码登录（不要预先编码 redirect_uri，urlencode 会处理）
    params = {
        "appid": CORPID,
        "agentid": AGENTID,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"https://open.work.weixin.qq.com/wwopen/sso/qrConnect?{urlencode(params)}"


def _build_mobile_oauth_url(redirect_uri: str, state: str = "") -> str:
    """Build WeCom OAuth URL for mobile app (silent auth)."""
    params = {
        "appid": CORPID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "snsapi_base",  # 静默授权
        "state": state,
        "agentid": AGENTID,
    }
    url = f"https://open.weixin.qq.com/connect/oauth2/authorize?{urlencode(params)}"
    return url + "#wechat_redirect"


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------

def _load_sessions() -> dict:
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_sessions(sessions: dict):
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f)


def _create_session(user_id: str, user_info: dict) -> str:
    session_id = secrets.token_urlsafe(32)
    sessions = _load_sessions()
    
    # Clean expired sessions
    sessions = {
        k: v for k, v in sessions.items()
        if v.get("expires_at", 0) > time.time()
    }
    
    sessions[session_id] = {
        "user_id": user_id,
        "user_info": user_info,
        "created_at": time.time(),
        "expires_at": time.time() + SESSION_EXPIRE_SECONDS,
    }
    
    _save_sessions(sessions)
    return session_id


def _validate_session(session_id: str) -> Optional[dict]:
    sessions = _load_sessions()
    session = sessions.get(session_id)
    
    if not session:
        return None
    
    if session.get("expires_at", 0) < time.time():
        del sessions[session_id]
        _save_sessions(sessions)
        return None
    
    return session


def _delete_session(session_id: str):
    sessions = _load_sessions()
    if session_id in sessions:
        del sessions[session_id]
        _save_sessions(sessions)


# ---------------------------------------------------------------------------
# Auth Middleware
# ---------------------------------------------------------------------------

class AuthMiddleware(BaseHTTPMiddleware):
    """Auth middleware supporting WeCom OAuth for admin, shared_secret for callbacks."""
    
    # Public paths (no auth required)
    PUBLIC_PATHS = {
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
    
    # OAuth callback paths
    OAUTH_PATHS = {
        "/api/wx-jsapi-ticket",
        "/api/wx-oauth",
        "/admin/login",
        "/admin/callback",
        "/admin/logout",
    }
    
    # Business callback paths (use shared_secret)
    CALLBACK_PREFIXES = ("/wecom", "/iot", "/alipay", "/iot-auth-callback", "/alipay-auth-callback")
    
    def __init__(self, app, config_manager):
        super().__init__(app)
        self._config = config_manager
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        
        # 1. Public paths skip auth
        if path in self.PUBLIC_PATHS:
            return await call_next(request)
        
        # 2. OAuth paths (login/callback/logout)
        if path in self.OAUTH_PATHS:
            return await call_next(request)
        
        # 3. Business callbacks use shared_secret
        for prefix in self.CALLBACK_PREFIXES:
            if path.startswith(prefix):
                mode = self._config.auth_mode
                if mode == "shared_secret":
                    expected = self._config.auth_shared_secret
                    provided = request.headers.get("x-dispatcher-secret", "")
                    if not expected or not hmac.compare_digest(provided, expected):
                        logger.warning(f"[auth] 鉴权失败: shared_secret mismatch")
                        return Response(content="unauthorized", status_code=401)
                return await call_next(request)
        
        # 4. Admin paths require OAuth authentication
        if path.startswith("/admin") or path.startswith("/api"):
            return await self._check_oauth(request, call_next)
        
        # 5. Default: deny
        return Response(content="not found", status_code=404)
    
    async def _check_oauth(self, request: Request, call_next: Callable) -> Response:
        """Check WeCom OAuth authentication."""
        path = request.url.path
        
        # Check session cookie
        session_id = request.cookies.get("admin_session")
        
        if session_id:
            session = _validate_session(session_id)
            if session:
                user_id = session.get("user_id")
                if user_id in ALLOWED_USERS:
                    request.state.user = session.get("user_info")
                    return await call_next(request)
                else:
                    return Response(content="forbidden - user not allowed", status_code=403)
        
        # Check if URL has OAuth code (WeCom redirect)
        code = request.query_params.get("code")
        if code:
            user_info = _get_user_info(code)
            if user_info and user_info.get("user_id"):
                user_id = user_info["user_id"]
                if user_id in ALLOWED_USERS:
                    session_id = _create_session(user_id, user_info)
                    redirect_url = str(request.url).split("?")[0]
                    response = Response(status_code=302, headers={"Location": redirect_url})
                    response.set_cookie(
                        key="admin_session",
                        value=session_id,
                        max_age=SESSION_EXPIRE_SECONDS,
                        httponly=True,
                        secure=True,
                    )
                    return response
                else:
                    return Response(content="forbidden - user not allowed", status_code=403)
        
        # Not authenticated, redirect to OAuth
        redirect_uri = f"https://{request.url.hostname}/admin/callback"
        state = secrets.token_urlsafe(16)
        
        user_agent = request.headers.get("user-agent", "")
        is_wecom = "wxwork" in user_agent.lower() or "micromessenger" in user_agent.lower()
        
        if is_wecom:
            # WeCom internal: silent auth
            oauth_url = _build_mobile_oauth_url(redirect_uri, state)
        else:
            # External: QR scan login
            oauth_url = _build_oauth_url(redirect_uri, state)
        
        return Response(status_code=302, headers={"Location": oauth_url})


# ---------------------------------------------------------------------------
# OAuth Routes (to be registered in dispatcher.py)
# ---------------------------------------------------------------------------

async def admin_login(request: Request) -> Response:
    """Admin login - redirect to OAuth."""
    redirect_uri = f"https://{request.url.hostname}/admin/callback"
    state = secrets.token_urlsafe(16)
    oauth_url = _build_oauth_url(redirect_uri, state)
    return Response(status_code=302, headers={"Location": oauth_url})


async def admin_callback(request: Request) -> Response:
    """OAuth callback - exchange code for session."""
    code = request.query_params.get("code")
    
    if not code:
        return Response(content="missing code", status_code=400)
    
    user_info = _get_user_info(code)
    
    if not user_info or not user_info.get("user_id"):
        return Response(content="failed to get user info", status_code=401)
    
    user_id = user_info["user_id"]
    
    if user_id not in ALLOWED_USERS:
        return Response(content="forbidden - user not allowed", status_code=403)
    
    session_id = _create_session(user_id, user_info)
    
    response = Response(status_code=302, headers={"Location": "/admin"})
    response.set_cookie(
        key="admin_session",
        value=session_id,
        max_age=SESSION_EXPIRE_SECONDS,
        httponly=True,
        secure=True,
    )
    
    logger.info(f"[oauth] 用户登录成功: {user_id}")
    return response


async def admin_logout(request: Request) -> Response:
    """Admin logout - clear session."""
    session_id = request.cookies.get("admin_session")
    
    if session_id:
        _delete_session(session_id)
    
    response = Response(status_code=302, headers={"Location": "/admin"})
    response.delete_cookie("admin_session")
    
    return response
