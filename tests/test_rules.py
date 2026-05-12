"""Tests for the rule engine."""
import pytest
from webhook_dispatcher.rules import RuleEngine


def test_match_first_rule():
    engine = RuleEngine([
        {"name": "a", "enabled": True, "conditions": {"MsgType": "text", "Content": "你好"}, "action": {"type": "forward"}},
        {"name": "b", "enabled": True, "conditions": {"MsgType": "text"}, "action": {"type": "ignore"}},
        {"name": "c", "enabled": True, "conditions": {}, "action": {"type": "forward"}},
    ])
    assert engine.match({"MsgType": "text", "Content": "你好"})["name"] == "a"
    assert engine.match({"MsgType": "text", "Content": "其他"})["name"] == "b"
    assert engine.match({"MsgType": "image"})["name"] == "c"


def test_match_empty_conditions():
    engine = RuleEngine([
        {"name": "default", "enabled": True, "conditions": {}, "action": {"type": "forward"}},
    ])
    assert engine.match({"anything": "here"})["name"] == "default"
    assert engine.match({})["name"] == "default"


def test_no_match():
    engine = RuleEngine([
        {"name": "a", "enabled": True, "conditions": {"MsgType": "image"}, "action": {"type": "forward"}},
    ])
    assert engine.match({"MsgType": "text"}) is None


def test_disabled_rule():
    engine = RuleEngine([
        {"name": "a", "enabled": False, "conditions": {"MsgType": "text"}, "action": {"type": "forward"}},
        {"name": "b", "enabled": True, "conditions": {}, "action": {"type": "ignore"}},
    ])
    assert engine.match({"MsgType": "text"})["name"] == "b"


def test_nested_conditions():
    engine = RuleEngine([
        {"name": "nested", "enabled": True, "conditions": {"ApprovalInfo.SpNo": "123"}, "action": {"type": "forward"}},
    ])
    assert engine.match({"ApprovalInfo": {"SpNo": "123"}})["name"] == "nested"
    assert engine.match({"ApprovalInfo": {"SpNo": "456"}}) is None


def test_exists_condition():
    engine = RuleEngine([
        {"name": "exists", "enabled": True, "conditions": {"Event": "exists"}, "action": {"type": "forward"}},
    ])
    assert engine.match({"Event": "subscribe"})["name"] == "exists"
    assert engine.match({"MsgType": "text"}) is None
