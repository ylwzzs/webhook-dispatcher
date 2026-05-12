"""Forwarder with retry and dead-letter support."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class Forwarder:
    """Forwards webhook events to Hermes with retry and dead-letter logging."""

    def __init__(self, config_manager, event_log):
        self._config = config_manager
        self._event_log = event_log
        self._http_client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=20.0)

    async def stop(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    @staticmethod
    def sign_hmac(secret: str, body: bytes) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    @staticmethod
    def apply_context(payload: dict, rule: dict) -> dict:
        context = rule.get("context", "")
        prompt = rule.get("prompt", "")
        if context or prompt:
            payload = {**payload}
            if context:
                payload["_rule_context"] = context
            if prompt:
                payload["_rule_prompt"] = prompt
        return payload

    async def forward(self, route_name: str, payload: dict,
                      target: Optional[str] = None) -> bool:
        dest = target or route_name
        url = self._config.hermes_base.format(route=dest)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        route_cfg = self._config.get_route(route_name) or {}
        secret = route_cfg.get("secret", self._config.hermes_secret)
        signature = self.sign_hmac(secret, body)
        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        }

        max_retries = self._config.max_retries
        retry_delay = self._config.retry_delay

        for attempt in range(max_retries + 1):
            try:
                resp = await self._http_client.post(url, content=body, headers=headers)
                if resp.status_code in (200, 201, 202):
                    logger.info(f"[forwarder] 转发成功 route={route_name} -> {dest} attempt={attempt}")
                    return True
                logger.warning(
                    f"[forwarder] 转发失败 route={route_name} status={resp.status_code} "
                    f"body={resp.text[:200]} attempt={attempt}"
                )
            except Exception as e:
                logger.error(f"[forwarder] 转发异常 route={route_name} attempt={attempt}: {e}")

            if attempt < max_retries:
                await asyncio.sleep(retry_delay * (attempt + 1))

        # All retries exhausted — dead letter
        self._event_log.log_dead_letter(route_name, payload, dest)
        logger.error(f"[forwarder] 转发最终失败 route={route_name} -> {dest}，已记录死信")
        return False
