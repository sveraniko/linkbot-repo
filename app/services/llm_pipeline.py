"""LLM pipeline service (Telegram-agnostic)
Provides a single entry to run the LLM with selected sources and user question.
Returns: (response_text, used_source_ids, metadata)
"""
from __future__ import annotations
import time
from typing import Tuple, List, Dict

from app.db import session_scope
from app.services.retrieval import load_selected_sources
from app.services.prompt_builder import build_system_prompt, build_context_prompt, build_user_prompt
from app.services.token_budget import calculate_token_budget
from app.services.llm import call_llm_with_retry, LLM_MAX_TOKENS_OUT, LLM_TEMPERATURE, LLM_TIMEOUT
from app.services.memory import get_preferred_model

async def run_llm_pipeline(
    user_id: int,
    selected_artifact_ids: List[int],
    question: str,
    run_id: str | None = None,
) -> Tuple[str, List[int], Dict]:
    # Read user's preferred model
    async with session_scope() as st:
        user_model = await get_preferred_model(st, user_id)

    # Budget for logs/diagnostics
    in_budget = calculate_token_budget(user_model, LLM_MAX_TOKENS_OUT)
    print(f"DEBUG LLM start: model={user_model} tokens_budget={in_budget}")

    # Load sources and build prompts
    sources, _total_tokens = await load_selected_sources(user_id, selected_artifact_ids)
    system_prompt = build_system_prompt()
    context_prompt = build_context_prompt(sources)
    user_prompt = build_user_prompt(question)

    # Call LLM
    response_text, metadata = await call_llm_with_retry(
        system_prompt=system_prompt,
        context_prompt=context_prompt,
        user_prompt=user_prompt,
        model=user_model,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS_OUT,
        timeout=LLM_TIMEOUT,
    )

    if not run_id:
        run_id = f"run-{int(time.time())}-{hash(question) % 10000}"

    metadata = {**metadata, "model": user_model}
    print(
        f"DEBUG LLM done: run_id={run_id} used_sources={selected_artifact_ids} len(text)={len(response_text)} duration_ms={metadata.get('duration_ms', 0)}"
    )
    return response_text, selected_artifact_ids, metadata
