"""Webhook rule engine — pure matching logic."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RuleEngine:
    """Match events against ordered rules and return the first match."""

    def __init__(self, rules: List[dict]):
        self._rules = [r for r in rules if r.get("enabled", True)]

    def match(self, payload: dict) -> Optional[dict]:
        for rule in self._rules:
            conditions = rule.get("conditions", {})
            match_not = rule.get("match_not", {})
            if self._match_conditions(conditions, payload):
                if match_not and self._match_conditions(match_not, payload):
                    # match_not 条件满足，跳过此规则
                    continue
                return rule
        return None

    def _match_conditions(self, conditions: dict, payload: dict) -> bool:
        if not conditions:
            return True
        for key, expected in conditions.items():
            actual = self._get_nested_value(payload, key)
            if expected == "exists":
                if actual is None:
                    return False
            elif isinstance(expected, list):
                # match_any: 值在列表中
                if actual is None or str(actual) not in [str(e) for e in expected]:
                    return False
            elif actual is None or str(actual) != str(expected):
                return False
        return True

    @staticmethod
    def _get_nested_value(data: dict, dotted_key: str):
        current = data
        for part in dotted_key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @property
    def rules(self) -> List[dict]:
        return list(self._rules)
