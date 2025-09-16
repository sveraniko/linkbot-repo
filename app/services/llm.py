"""LLM service for handling model calls with proper configuration and error handling."""
import os
import logging
import asyncio
import time
from typing import Tuple, List, Dict, Any, Optional
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.config import settings

logger = logging.getLogger(__name__)

# LLM configuration from environment variables
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_MAX_TOKENS_OUT = int(os.getenv("LLM_MAX_TOKENS_OUT", "2048"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_DISABLED = os.getenv("LLM_DISABLED", "0") == "1"

# Initialize OpenAI client
client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

async def call_llm(
    system_prompt: str,
    context_prompt: str,
    user_prompt: str,
    model: str = LLM_MODEL,
    temperature: float = LLM_TEMPERATURE,
    max_tokens: int = LLM_MAX_TOKENS_OUT,
    timeout: int = LLM_TIMEOUT
) -> Tuple[str, Dict[str, Any]]:
    """
    Call the LLM with the provided prompts.
    
    Returns:
        Tuple of (response_text, metadata)
    """
    if LLM_DISABLED:
        return "LLM functionality is currently disabled.", {}
    
    if not client:
        raise ValueError("OpenAI API key not configured")
    
    # Combine prompts
    messages: List[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context_prompt + "\n\n" + user_prompt}
    ]
    
    start_time = time.time()
    
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout
        )
        
        duration = time.time() - start_time
        response_text = response.choices[0].message.content or ""
        
        metadata = {
            "model": model,
            "tokens_in": response.usage.prompt_tokens if response.usage else 0,
            "tokens_out": response.usage.completion_tokens if response.usage else 0,
            "duration_ms": int(duration * 1000)
        }
        
        logger.info(f"LLM call completed: {metadata}")
        return response_text, metadata
        
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"LLM call failed after {duration:.2f}s: {e}")
        raise

async def call_llm_with_retry(
    system_prompt: str,
    context_prompt: str,
    user_prompt: str,
    model: str = LLM_MODEL,
    temperature: float = LLM_TEMPERATURE,
    max_tokens: int = LLM_MAX_TOKENS_OUT,
    timeout: int = LLM_TIMEOUT,
    max_retries: int = 2
) -> Tuple[str, Dict[str, Any]]:
    """
    Call the LLM with retry logic for handling rate limits and temporary errors.
    """
    last_exception: Optional[Exception] = None
    
    for attempt in range(max_retries + 1):
        try:
            return await call_llm(
                system_prompt, context_prompt, user_prompt,
                model, temperature, max_tokens, timeout
            )
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                # Check if it's a retryable error (rate limit, server error)
                error_str = str(e).lower()
                if "rate limit" in error_str or "429" in error_str or "500" in error_str:
                    # Exponential backoff
                    wait_time = (2 ** attempt) + (attempt * 0.1)
                    logger.warning(f"LLM call failed (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Non-retryable error
                    break
            else:
                # Max retries exceeded
                break
    
    # If we get here, all retries failed
    if last_exception is not None:
        raise last_exception
    else:
        raise Exception("LLM call failed")