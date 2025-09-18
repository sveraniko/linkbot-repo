"""
Telemetry/event logging helpers (JSONL to stdout)
Usage:
    from app.services.telemetry import event, error
    event("llm_start", user_id=..., chat_id=..., model=..., tokens_budget=...)
    event("llm_done", user_id=..., chat_id=..., run_id=..., used_sources=[...], text_len=..., duration_ms=..., tokens_in=..., tokens_out=...)
    error("handler_exception", user_id=..., chat_id=..., err=str(e), where="ask:answer:...")
"""
from __future__ import annotations
import json, time, sys
from typing import Any, Dict

_DEF_STREAM = sys.stdout

def _now_ms() -> int:
    return int(time.time() * 1000)

def event(action: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {
        "ts": _now_ms(),
        "action": action,
    }
    payload.update(fields)
    try:
        _DEF_STREAM.write(json.dumps(payload, ensure_ascii=False) + "\n")
        _DEF_STREAM.flush()
    except Exception:
        # Fallback to print to avoid breaking handlers
        print(payload)

def error(action: str, **fields: Any) -> None:
    fields["level"] = "error"
    event(action, **fields)
