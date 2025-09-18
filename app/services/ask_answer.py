"""ASK answer rendering and bar helpers (Telegram-agnostic APIs)
- build_context_line(project_name, scope, model, budget, cost)
- build_sources_short(first_title, first_id)
- compute_cost(model, ti, to) -> float
"""
from __future__ import annotations
from typing import Optional

_PRICING = {
    "gpt-5": (0.002, 0.006),
    "gpt-5-mini": (0.0010, 0.0030),
    "gpt-5-nano": (0.0002, 0.0006),
    "gpt-4.1": (0.005, 0.015),
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.0005, 0.0015),
}

_DEF = (0.002, 0.006)

def compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    mk = (model or "").lower()
    in1k, out1k = _DEF
    for key, pair in _PRICING.items():
        if mk.startswith(key):
            in1k, out1k = pair
            break
    return (tokens_in / 1000.0) * in1k + (tokens_out / 1000.0) * out1k

def build_context_line(project_name: str, scope: str, model: str, budget: int, cost: float) -> str:
    return f"Project: {project_name} • Scope: {scope} • Model: {model} Budget: ~{budget} • ≈ ${cost:.4f}"

def build_sources_short(first_title: str, first_id: int) -> str:
    short = first_title[:18] + (" …" if len(first_title) > 18 else "")
    return f"📚 Sources: [#{short} id{first_id}]"
