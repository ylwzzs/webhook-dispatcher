"""Unified configuration manager with hot-reload for all config items."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from webhook_dispatcher.decryptors import DECRYPTORS

logger = logging.getLogger(__name__)


class ConfigManager:
    """Hot-reloadable configuration manager.

    Monitors config.yaml mtime and reloads routes, credentials,
    decryptors, and hermes settings on change.
    """

    def __init__(self, config_path: str):
        self._path = Path(config_path)
        self._last_mtime: float = 0.0
        self._raw: dict = {}
        self._server: dict = {}
        self._hermes_base: str = ""
        self._hermes_secret: str = ""
        self._credentials: Dict[str, dict] = {}
        self._routes: Dict[str, dict] = {}
        self._decryptors: Dict[str, Any] = {}
        self._rule_engines: Dict[str, Any] = {}
        self._forward: dict = {}
        self._auth: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            mtime = self._path.stat().st_mtime
            if mtime <= self._last_mtime:
                return
            with open(self._path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            # Build all new state into local vars first (atomic swap)
            server = raw.get("server", {})
            hermes_cfg = raw.get("hermes", {})
            hermes_base = hermes_cfg.get(
                "webhook_url", "http://127.0.0.1:8644/webhooks/{route}"
            )
            hermes_secret = hermes_cfg.get("default_secret", "")
            credentials = raw.get("credentials", {})
            routes = raw.get("routes", {})
            forward = raw.get("forward", {})
            auth = server.get("auth", {})

            # Rebuild decryptor instances
            new_decryptors: Dict[str, Any] = {}
            for name, rc in routes.items():
                cred_name = rc.get("credentials")
                if cred_name:
                    cred = credentials.get(cred_name)
                    if not cred:
                        logger.error(f"[config] 路由 {name}: 凭据 '{cred_name}' 不存在")
                        continue
                    dec_type = cred.get("type", "")
                    cls = DECRYPTORS.get(dec_type)
                    if cls:
                        try:
                            new_decryptors[name] = cls(cred)
                            logger.info(f"[config] 路由 {name}: 解密器 {dec_type} (凭据: {cred_name})")
                        except Exception as e:
                            logger.error(f"[config] 路由 {name}: 解密器加载失败: {e}")

            # Build rule engines per route (支持 rule_groups)
            from webhook_dispatcher.rules import RuleEngine
            new_rule_engines: Dict[str, Any] = {}
            total_rules = 0
            for route_name, route_config in routes.items():
                # 支持 rule_groups 和 rules 两种格式
                if "rule_groups" in route_config:
                    # 新格式：rule_groups
                    flat_rules = []
                    for group in route_config.get("rule_groups", []):
                        group_enabled = group.get("enabled", True)
                        group_name = group.get("name", "")
                        for rule in group.get("rules", []):
                            rule["_group"] = group_name
                            rule["_group_enabled"] = group_enabled
                            # 如果组被禁用，规则也禁用
                            if not group_enabled:
                                rule["enabled"] = False
                            flat_rules.append(rule)
                    routes[route_name]["rules"] = flat_rules  # 兼容旧逻辑
                    if flat_rules:
                        new_rule_engines[route_name] = RuleEngine(flat_rules)
                        total_rules += len(flat_rules)
                elif "rules" in route_config:
                    # 旧格式：rules
                    rules = route_config.get("rules", [])
                    if rules:
                        new_rule_engines[route_name] = RuleEngine(rules)
                        total_rules += len(rules)
            logger.info(f"[config] 配置已加载: {len(routes)} 路由, {len(new_decryptors)} 解密器, {total_rules} 规则")

            # Atomic swap — all or nothing
            self._raw = raw
            self._server = server
            self._hermes_base = hermes_base
            self._hermes_secret = hermes_secret
            self._credentials = credentials
            self._routes = routes
            self._decryptors = new_decryptors
            self._rule_engines = new_rule_engines
            self._forward = forward
            self._auth = auth
            self._last_mtime = mtime

        except Exception as e:
            logger.error(f"[config] 加载配置失败: {e}")

    def maybe_reload(self) -> None:
        self._load()

    @property
    def raw(self) -> dict:
        return self._raw

    @property
    def server(self) -> dict:
        return self._server

    @property
    def host(self) -> str:
        return self._server.get("host", "0.0.0.0")

    @property
    def port(self) -> int:
        return int(self._server.get("port", 8646))

    @property
    def hermes_base(self) -> str:
        return self._hermes_base

    @property
    def hermes_secret(self) -> str:
        return self._hermes_secret

    @property
    def credentials(self) -> Dict[str, dict]:
        return self._credentials

    @property
    def routes(self) -> Dict[str, dict]:
        return self._routes

    @property
    def decryptors(self) -> Dict[str, Any]:
        return self._decryptors

    @property
    def rule_engines(self) -> Dict[str, Any]:
        return self._rule_engines

    @property
    def forward_config(self) -> dict:
        return self._forward

    @property
    def max_retries(self) -> int:
        return int(self._forward.get("max_retries", 2))

    @property
    def retry_delay(self) -> float:
        return float(self._forward.get("retry_delay", 1))

    @property
    def auth_config(self) -> dict:
        return self._auth

    @property
    def auth_mode(self) -> str:
        return self._auth.get("mode", "none")

    @property
    def auth_shared_secret(self) -> str:
        return self._auth.get("shared_secret", "")

    @property
    def auth_ip_whitelist(self) -> list:
        return self._auth.get("ip_whitelist", ["127.0.0.1", "::1"])

    def get_route(self, name: str) -> Optional[dict]:
        return self._routes.get(name)

    def get_decryptor(self, route_name: str) -> Optional[Any]:
        return self._decryptors.get(route_name)

    def get_rule_engine(self, route_name: str) -> Optional[Any]:
        return self._rule_engines.get(route_name)

    def save(self) -> None:
        """Write current config state back to config.yaml and force reload."""
        try:
            self._raw.setdefault("server", self._server)
            self._raw.setdefault("hermes", {})
            self._raw["credentials"] = self._credentials
            self._raw["routes"] = self._routes
            self._raw["server"]["auth"] = self._auth
            with open(self._path, "w", encoding="utf-8") as f:
                yaml.dump(self._raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            self._last_mtime = 0.0  # Force reload on next access
            logger.info("[config] 配置已保存")
        except Exception as e:
            logger.error(f"[config] 保存配置失败: {e}")
            raise
