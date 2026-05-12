"""JSONL event log with daily rotation and dead-letter support."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from webhook_dispatcher import HERMES_HOME

DEFAULT_LOG_DIR = HERMES_HOME / "plugins/webhook-dispatcher/logs"
RETENTION_DAYS = 7
CLEANUP_INTERVAL = 3600  # Run cleanup every hour


class EventLog:
    """Append-only JSONL event log with daily rotation and auto-cleanup."""

    def __init__(self, log_dir: Optional[str] = None):
        self._log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._stats: Dict[str, int] = {
            "total": 0, "forward_ok": 0, "forward_fail": 0,
            "ignored": 0, "dead_letters": 0,
        }
        self._last_cleanup: float = 0.0
        self._cleanup_old_logs()

    def _maybe_cleanup(self) -> None:
        now = time.time()
        if now - self._last_cleanup < CLEANUP_INTERVAL:
            return
        self._cleanup_old_logs()
        self._last_cleanup = now

    def _today_file(self) -> Path:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return self._log_dir / f"events-{date_str}.jsonl"

    def _dead_letter_file(self) -> Path:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return self._log_dir / f"dead-{date_str}.jsonl"

    def _cleanup_old_logs(self) -> None:
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
        for f in self._log_dir.glob("events-*.jsonl"):
            try:
                date_str = f.stem.replace("events-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    f.unlink()
            except (ValueError, OSError):
                pass
        for f in self._log_dir.glob("dead-*.jsonl"):
            try:
                date_str = f.stem.replace("dead-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    f.unlink()
            except (ValueError, OSError):
                pass
        self._last_cleanup = time.time()

    def log_event(self, route: str, event_type: str, rule: str,
                  action: str, forward_ok: bool, duration_ms: int,
                  extra: Optional[Dict[str, Any]] = None) -> None:
        self._maybe_cleanup()

        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "route": route,
            "event": event_type,
            "rule": rule,
            "action": action,
            "ok": forward_ok,
            "ms": duration_ms,
        }
        if extra:
            entry.update(extra)

        self._stats["total"] += 1
        if action == "ignore":
            self._stats["ignored"] += 1
        elif forward_ok:
            self._stats["forward_ok"] += 1
        else:
            self._stats["forward_fail"] += 1

        try:
            with open(self._today_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[eventlog] 写入事件日志失败: {e}")

    def log_dead_letter(self, route: str, payload: dict, target: str) -> None:
        self._maybe_cleanup()

        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "route": route,
            "target": target,
            "payload": payload,
        }
        self._stats["dead_letters"] += 1
        try:
            with open(self._dead_letter_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[eventlog] 写入死信日志失败: {e}")

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def get_recent_events(self, limit: int = 10) -> list:
        try:
            log_file = self._today_file()
            if not log_file.exists():
                return []
            lines = log_file.read_text(encoding="utf-8").strip().split("\n")
            events = []
            for line in reversed(lines[-limit * 2:]):
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if len(events) >= limit:
                        break
            return events
        except Exception:
            return []
