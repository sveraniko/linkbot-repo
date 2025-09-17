from __future__ import annotations

import os
LLM_DISABLED = os.getenv("LLM_DISABLED", "1") == "1"  # 1 = OFF by default

import asyncio
import logging
import json
from typing import Sequence, List, Dict

from app.config import settings

logger = logging.getLogger(__name__)

# Try to import OpenAI
try:
    from openai import AsyncOpenAI
    from openai.types.chat import ChatCompletionMessageParam
    _client = AsyncOpenAI(api_key=settings.openai_api_key)
    OPENAI_AVAILABLE = True
except ImportError:
    _client = None
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI package not installed. LLM features will not work.")

SYSTEM_BASE = (
    "Ты — инженер-ассистент по проекту. Используй предоставленный контекст строго как факты проекта. "
    "Если в контексте нет нужных данных, честно скажи об этом, а затем ответь общими знаниями, "
    "не выдумывая проектные детали. Отвечай конкретно, структурировано. Не раскрывай ход рассуждений."
)

def _make_messages(prompt: str, ctx_chunks: Sequence[str]) -> list[ChatCompletionMessageParam]:
    ctx_block = ""
    if ctx_chunks:
        # Жёстко отделяем контекст, чтобы модель не «мешала» его с инструкциями
        joined = "\n\n---\n".join(ctx_chunks)
        ctx_block = f"\n\nКонтекст проекта (фрагменты):\n---\n{joined}\n---"
    system = SYSTEM_BASE + ctx_block
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

async def ask_llm(prompt: str, ctx_chunks: Sequence[str], model: str = "gpt-4o", max_tokens: int = 1200) -> str:
    """
    Основной ответ. model: 'gpt-4o' | 'gpt-4o-mini'
    """
    if LLM_DISABLED:
        ctx_n = len(ctx_chunks or [])
        return f"🧪 TEST: LLM отключён.\nВопрос: {prompt[:400]}\nКонтекст: {ctx_n} фрагм."
    
    if not OPENAI_AVAILABLE or _client is None:
        return "⚠️ OpenAI SDK не установлен. Установите openai>=1.40.0"
    
    messages = _make_messages(prompt, ctx_chunks)
    use_model = "gpt-4o-mini" if model == "gpt-4o-mini" else "gpt-4o"

    try:
        # Chat Completions — надёжно и просто.
        resp = await _client.chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.exception("LLM error: %s", e)
        return "⚠️ Не удалось получить ответ от модели. Попробуй ещё раз или проверь ключ/лимиты."

async def summarize_text(text: str, model: str | None = None, max_tokens: int = 500) -> str:
    """
    Краткое резюме ответа (используется кнопкой 📌 Summary).
    """
    if LLM_DISABLED:
        return "🧪 TEST: LLM отключён. Резюме не создано."
        
    if not OPENAI_AVAILABLE or _client is None:
        return "⚠️ OpenAI SDK не установлен. Установите openai>=1.40.0"
        
    model = model or "gpt-4o"
    prompt = (
        "Сделай сжатое, фактологичное резюме текста ниже: 5–10 пунктов или ~120–200 слов. "
        "Без рассуждений, только итог.\n\nТекст:\n" + text
    )
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_BASE},
        {"role": "user", "content": prompt}
    ]
    try:
        resp = await _client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.exception("Summarize error: %s", e)
        return "⚠️ Не удалось сделать краткое резюме."

async def generate_zip_files(task_description: str, context_chunks: Sequence[str], tags: List[str]) -> Dict[str, str]:
    """
    Generate multiple files for ZIP archive based on task description.
    
    Args:
        task_description: Description of what to generate
        context_chunks: Project context
        tags: Tags for filtering/guidance
        
    Returns:
        Dictionary mapping file paths to their content
    """
    if LLM_DISABLED:
        return {"test.txt": "🧪 TEST: LLM отключён. Файлы не созданы."}
        
    if not OPENAI_AVAILABLE or _client is None:
        return {"error.txt": "OpenAI SDK не установлен. Установите openai>=1.40.0"}
    
    # Prepare context and prompt
    context = "\n\n".join(context_chunks[:30])
    tags_str = ", ".join(tags) if tags else "general"
    
    prompt = f"""
Вы — эксперт-разработчик. Создайте архив файлов для задачи.

КОНТЕКСТ ПРОЕКТА:
{context}

ЗАДАЧА: {task_description}
ТЕГИ: {tags_str}

ИНСТРУКЦИЕ:
1. Проанализируйте контекст проекта
2. Создайте необходимые файлы для выполнения задачи
3. Верните JSON в формате: {{"files": {{"path/file.ext": "content", ...}}, "notes": "описание изменений"}}
4. Пути файлов должны быть относительными
5. Включите только необходимые файлы
6. Добавьте комментарии в код

ОТВЕТ (JSON):
"""
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_BASE},
        {"role": "user", "content": prompt}
    ]

    try:
        # Call OpenAI API
        resp = await _client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.3,
            max_tokens=2000,
        )
        
        # Parse the response
        content = (resp.choices[0].message.content or "").strip()
        
        # Try to parse as JSON
        try:
            result = json.loads(content)
            return result.get("files", {})
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON from LLM response")
            return {"error.txt": f"Failed to parse response: {content}"}
            
    except Exception as e:
        logger.exception("generate_zip_files error: %s", e)
        return {"error.txt": f"Error generating files: {str(e)}"}

async def generate_single_file(file_path: str, task_description: str, context_chunks: Sequence[str]) -> str:
    """
    Generate content for a single file.
    
    Args:
        file_path: Target file path
        task_description: What to generate
        context_chunks: Project context
        
    Returns:
        File content as string
    """
    if LLM_DISABLED:
        return "# 🧪 TEST: LLM отключён. Файл не создан."
        
    if not OPENAI_AVAILABLE or _client is None:
        return "# OpenAI SDK не установлен. Установите openai>=1.40.0"
        
    context = "\n\n".join(context_chunks[:30])
    
    prompt = f"""
Вы — эксперт-разработчик. Создайте файл по указанному пути.

КОНТЕКСТ ПРОЕКТА:
{context}

ФАЙЛ: {file_path}
ЗАДАЧА: {task_description}

ИНСТРУКЦИЕ:
1. Проанализируйте контекст проекта
2. Создайте содержимое файла по указанному пути
3. Учтите расширение файла для выбора языка/формата
4. Добавьте необходимые комментарии
5. Следуйте лучшим практикам

СОДЕРЖИМОЕ ФАЙЛА:
"""
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_BASE},
        {"role": "user", "content": prompt}
    ]

    try:
        # Call OpenAI API
        resp = await _client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.3,
            max_tokens=1500,
        )
        
        return (resp.choices[0].message.content or "").strip()
        
    except Exception as e:
        logger.exception("generate_single_file error: %s", e)
        return f"# Error generating content: {str(e)}"

async def analyze_diff_context(summary: str, context_chunks: Sequence[str]) -> str:
    """
    Analyze diff summary with project context to provide insights.
    
    Args:
        summary: Diff summary
        context_chunks: Project context
        
    Returns:
        Analysis and recommendations
    """
    if LLM_DISABLED:
        return "🧪 TEST: LLM отключён. Анализ не выполнен."
        
    if not OPENAI_AVAILABLE or _client is None:
        return "⚠️ OpenAI SDK не установлен. Установите openai>=1.40.0"
        
    context = "\n\n".join(context_chunks[:20])
    
    prompt = f"""
Вы — эксперт-аналитик кода. Проанализируйте изменения в проекте.

КОНТЕКСТ ПРОЕКТА:
{context}

ИЗМЕНЕНИЯ:
{summary}

ИНСТРУКЦИЕ:
1. Проанализируйте характер изменений
2. Оцените потенциальное влияние на проект
3. Предложите рекомендации
4. Выделите важные моменты

АНАЛИЗ:
"""
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_BASE},
        {"role": "user", "content": prompt}
    ]

    try:
        # Call OpenAI API
        resp = await _client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.2,
            max_tokens=1000,
        )
        
        return (resp.choices[0].message.content or "").strip()
        
    except Exception as e:
        logger.exception("analyze_diff_context error: %s", e)
        return f"⚠️ Ошибка при анализе изменений: {str(e)}"