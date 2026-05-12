"""Admin API endpoints for managing webhook dispatcher configuration."""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _get_config(request: Request):
    return request.app.state.config_mgr


def _get_event_log(request: Request):
    return request.app.state.event_log


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CredentialItem(BaseModel):
    name: str
    type: str
    config: Dict[str, Any]

class RouteItem(BaseModel):
    name: str
    description: str = ""
    credentials: Optional[str] = None
    secret: str = ""
    forward_to: str = ""
    decrypt_optional: bool = False
    rules: List[dict] = []

class RuleItem(BaseModel):
    name: str
    enabled: bool = True
    conditions: Dict[str, str] = {}
    action: Dict[str, Any] = {}
    context: str = ""
    prompt: str = ""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@router.get("/credentials")
async def list_credentials(request: Request):
    mgr = _get_config(request)
    result = []
    for name, cfg in mgr.credentials.items():
        result.append({"name": name, "type": cfg.get("type", ""), "config": cfg})
    return result


@router.post("/credentials")
async def create_credential(item: CredentialItem, request: Request):
    mgr = _get_config(request)
    if item.name in mgr.credentials:
        raise HTTPException(400, f"凭据 '{item.name}' 已存在")
    cfg = dict(item.config)
    cfg["type"] = item.type
    mgr._credentials[item.name] = cfg
    mgr.save()
    return {"ok": True, "name": item.name}


@router.put("/credentials/{name}")
async def update_credential(name: str, item: CredentialItem, request: Request):
    mgr = _get_config(request)
    if name not in mgr.credentials:
        raise HTTPException(404, f"凭据 '{name}' 不存在")
    cfg = dict(item.config)
    cfg["type"] = item.type
    mgr._credentials[item.name] = cfg
    mgr.save()
    return {"ok": True}


@router.delete("/credentials/{name}")
async def delete_credential(name: str, request: Request):
    mgr = _get_config(request)
    if name not in mgr.credentials:
        raise HTTPException(404, f"凭据 '{name}' 不存在")
    # Check if any route references this credential
    for route_name, rc in mgr.routes.items():
        if rc.get("credentials") == name:
            raise HTTPException(400, f"路由 '{route_name}' 仍在使用此凭据，请先解除关联")
    del mgr._credentials[name]
    mgr.save()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/routes")
async def list_routes(request: Request):
    mgr = _get_config(request)
    result = []
    for name, rc in mgr.routes.items():
        entry = dict(rc)
        entry["name"] = name
        # Count rules
        rules = rc.get("rules", [])
        entry["rule_count"] = len(rules)
        # Decryptor status
        dec = mgr.get_decryptor(name)
        entry["decryptor"] = type(dec).__name__ if dec else None
        result.append(entry)
    return result


@router.post("/routes")
async def create_route(item: RouteItem, request: Request):
    mgr = _get_config(request)
    if item.name in mgr.routes:
        raise HTTPException(400, f"路由 '{item.name}' 已存在")
    if item.credentials and item.credentials not in mgr.credentials:
        raise HTTPException(400, f"凭据 '{item.credentials}' 不存在")
    route_cfg = {
        "description": item.description,
        "forward_to": item.forward_to or item.name,
        "rules": item.rules,
    }
    if item.credentials:
        route_cfg["credentials"] = item.credentials
    if item.secret:
        route_cfg["secret"] = item.secret
    if item.decrypt_optional:
        route_cfg["decrypt_optional"] = True
    mgr._routes[item.name] = route_cfg
    mgr.save()
    return {"ok": True, "name": item.name}


@router.put("/routes/{name}")
async def update_route(name: str, item: RouteItem, request: Request):
    mgr = _get_config(request)
    if name not in mgr.routes:
        raise HTTPException(404, f"路由 '{name}' 不存在")
    if item.credentials and item.credentials not in mgr.credentials:
        raise HTTPException(400, f"凭据 '{item.credentials}' 不存在")
    route_cfg = {
        "description": item.description,
        "forward_to": item.forward_to or name,
        "rules": item.rules,
    }
    if item.credentials:
        route_cfg["credentials"] = item.credentials
    if item.secret:
        route_cfg["secret"] = item.secret
    if item.decrypt_optional:
        route_cfg["decrypt_optional"] = True
    mgr._routes[name] = route_cfg
    mgr.save()
    return {"ok": True}


@router.delete("/routes/{name}")
async def delete_route(name: str, request: Request):
    mgr = _get_config(request)
    if name not in mgr.routes:
        raise HTTPException(404, f"路由 '{name}' 不存在")
    del mgr._routes[name]
    mgr.save()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@router.get("/routes/{route_name}/rules")
async def list_rules(route_name: str, request: Request):
    mgr = _get_config(request)
    rc = mgr.get_route(route_name)
    if not rc:
        raise HTTPException(404, f"路由 '{route_name}' 不存在")
    return rc.get("rules", [])


@router.post("/routes/{route_name}/rules")
async def add_rule(route_name: str, rule: RuleItem, request: Request):
    mgr = _get_config(request)
    rc = mgr.get_route(route_name)
    if not rc:
        raise HTTPException(404, f"路由 '{route_name}' 不存在")

    new_rule = {
        "name": rule.name,
        "enabled": rule.enabled,
        "conditions": rule.conditions,
        "action": rule.action,
    }
    if rule.context:
        new_rule["context"] = rule.context
    if rule.prompt:
        new_rule["prompt"] = rule.prompt

    # Conflict detection
    conflicts = _check_conflicts(new_rule, rc.get("rules", []))
    if conflicts:
        return {"ok": False, "conflicts": conflicts, "rule": new_rule}

    # Insert before default-forward (last rule with empty conditions)
    rules = rc.setdefault("rules", [])
    insert_idx = len(rules)
    for i, r in enumerate(rules):
        if not r.get("conditions") or r.get("conditions") == {}:
            insert_idx = i
            break
    rules.insert(insert_idx, new_rule)
    mgr.save()
    return {"ok": True, "name": rule.name}


@router.put("/routes/{route_name}/rules/{rule_name}")
async def update_rule(route_name: str, rule_name: str, rule: RuleItem, request: Request):
    mgr = _get_config(request)
    rc = mgr.get_route(route_name)
    if not rc:
        raise HTTPException(404, f"路由 '{route_name}' 不存在")
    rules = rc.get("rules", [])
    idx = None
    for i, r in enumerate(rules):
        if r.get("name") == rule_name:
            idx = i
            break
    if idx is None:
        raise HTTPException(404, f"规则 '{rule_name}' 不存在")

    updated = {
        "name": rule.name,
        "enabled": rule.enabled,
        "conditions": rule.conditions,
        "action": rule.action,
    }
    if rule.context:
        updated["context"] = rule.context
    if rule.prompt:
        updated["prompt"] = rule.prompt

    # Check conflicts against other rules (excluding self)
    other_rules = [r for i, r in enumerate(rules) if i != idx]
    conflicts = _check_conflicts(updated, other_rules)
    if conflicts:
        return {"ok": False, "conflicts": conflicts, "rule": updated}

    rules[idx] = updated
    mgr.save()
    return {"ok": True}


@router.delete("/routes/{route_name}/rules/{rule_name}")
async def delete_rule(route_name: str, rule_name: str, request: Request):
    mgr = _get_config(request)
    rc = mgr.get_route(route_name)
    if not rc:
        raise HTTPException(404, f"路由 '{route_name}' 不存在")
    rules = rc.get("rules", [])
    new_rules = [r for r in rules if r.get("name") != rule_name]
    if len(new_rules) == len(rules):
        raise HTTPException(404, f"规则 '{rule_name}' 不存在")
    rc["rules"] = new_rules
    mgr.save()
    return {"ok": True}


@router.put("/routes/{route_name}/rules/reorder")
async def reorder_rules(route_name: str, order: List[str], request: Request):
    mgr = _get_config(request)
    rc = mgr.get_route(route_name)
    if not rc:
        raise HTTPException(404, f"路由 '{route_name}' 不存在")
    rules = rc.get("rules", [])
    rule_map = {r.get("name"): r for r in rules}
    reordered = []
    for name in order:
        if name in rule_map:
            reordered.append(rule_map[name])
    # Add any rules not in the order list at the end
    seen = set(order)
    for r in rules:
        if r.get("name") not in seen:
            reordered.append(r)
    rc["rules"] = reordered
    mgr.save()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------

@router.get("/events")
async def get_events(request: Request, limit: int = 50):
    elog = _get_event_log(request)
    return elog.get_recent_events(limit)


@router.get("/stats")
async def get_stats(request: Request):
    mgr = _get_config(request)
    elog = _get_event_log(request)
    return {
        "routes": len(mgr.routes),
        "credentials": len(mgr.credentials),
        "rules": sum(len(rc.get("rules", [])) for rc in mgr.routes.values()),
        "events": elog.stats,
    }


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def _check_conflicts(new_rule: dict, existing_rules: list) -> list:
    new_conds = new_rule.get("conditions", {})
    new_action = new_rule.get("action", {}).get("type", "")
    conflicts = []

    for existing in existing_rules:
        ex_conds = existing.get("conditions", {})
        ex_action = existing.get("action", {}).get("type", "")
        ex_name = existing.get("name", "unknown")

        # Same conditions, different action
        if new_conds == ex_conds and new_action != ex_action:
            conflicts.append({
                "rule": ex_name,
                "type": "互斥",
                "detail": f"条件相同但动作不同({ex_action} vs {new_action})，后写的规则永远不命中",
            })

        # New rule is more specific (subset of existing conditions)
        elif new_conds != ex_conds and new_conds.items() >= ex_conds.items():
            if new_action != ex_action:
                conflicts.append({
                    "rule": ex_name,
                    "type": "遮蔽风险",
                    "detail": f"新规则更具体，若排在 '{ex_name}' 之后会被吞掉",
                })

        # New rule is more broad (superset of existing conditions)
        elif new_conds != ex_conds and ex_conds.items() <= new_conds.items():
            if new_action != ex_action:
                conflicts.append({
                    "rule": ex_name,
                    "type": "遮蔽现有规则",
                    "detail": f"新规则更宽泛，插在前面会导致 '{ex_name}' 失效",
                })

        # Partial overlap
        elif set(new_conds.keys()) & set(ex_conds.keys()):
            overlap_keys = set(new_conds.keys()) & set(ex_conds.keys())
            if all(new_conds.get(k) == ex_conds.get(k) for k in overlap_keys):
                if new_action != ex_action:
                    conflicts.append({
                        "rule": ex_name,
                        "type": "部分重叠",
                        "detail": f"条件在 {overlap_keys} 上重叠",
                    })

    return conflicts
