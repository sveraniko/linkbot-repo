"""Token budget service for managing context limits."""
import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Model context limits (approximate)
MODEL_CONTEXT_LIMITS = {
    "gpt-4o-mini": 128000,
    "gpt-4o": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
}

# Default values
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_CONTEXT_LIMIT = 128000
DEFAULT_MAX_TOKENS_OUT = 2048
DEFAULT_SYSTEM_TOKENS = 300  # Approximate tokens for system prompt
DEFAULT_MARGIN = 1000  # Safety margin

def get_model_context_limit(model: str) -> int:
    """
    Get the context limit for a given model.
    
    Args:
        model: Model name
        
    Returns:
        Context limit in tokens
    """
    return MODEL_CONTEXT_LIMITS.get(model, DEFAULT_CONTEXT_LIMIT)

def calculate_token_budget(
    model: str = DEFAULT_MODEL,
    max_tokens_out: int = DEFAULT_MAX_TOKENS_OUT,
    system_tokens: int = DEFAULT_SYSTEM_TOKENS,
    margin: int = DEFAULT_MARGIN
) -> int:
    """
    Calculate the available token budget for input context.
    
    Args:
        model: Model name
        max_tokens_out: Maximum tokens for output
        system_tokens: Tokens reserved for system prompt
        margin: Safety margin
        
    Returns:
        Available tokens for input context
    """
    context_limit = get_model_context_limit(model)
    in_budget = context_limit - max_tokens_out - system_tokens - margin
    return max(0, in_budget)  # Ensure non-negative

def allocate_budget_per_source(total_budget: int, num_sources: int) -> int:
    """
    Allocate token budget per source.
    
    Args:
        total_budget: Total available budget
        num_sources: Number of sources
        
    Returns:
        Budget per source
    """
    if num_sources <= 0:
        return total_budget
    return total_budget // num_sources

def handle_context_overflow(
    sources: list,
    token_budget: int,
    model: str = DEFAULT_MODEL
) -> Dict[str, Any]:
    """
    Handle context overflow by gracefully degrading the input.
    
    Args:
        sources: List of sources
        token_budget: Available token budget
        model: Model name
        
    Returns:
        Dictionary with handling strategy
    """
    # For now, we'll just return a strategy to reduce content
    # In a more advanced implementation, we could implement specific reduction strategies
    return {
        "strategy": "reduce_content",
        "message": "Question too large, reducing context",
        "original_budget": token_budget,
        "reduced_budget": max(1000, token_budget // 2)  # Reduce by half but keep minimum
    }