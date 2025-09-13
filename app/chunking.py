from dataclasses import dataclass
from app.tokenizer import make_chunks as make_token_chunks, count_tokens
from typing import List

@dataclass
class ChunkParams:
    size: int = 1600
    overlap: int = 150
    model: str = "gpt-3.5-turbo"

# Обновленная функция чанкинга на основе токенов
def make_chunks(text: str, size: int = 1600, overlap: int = 150, model: str = "gpt-3.5-turbo") -> List[str]:
    """
    Создает чанки текста на основе токенов tiktoken.
    Полностью заменяет старый посимвольный метод.
    """
    return make_token_chunks(text, model, size, overlap)

# Оставляем старую функцию для обратной совместимости (для тестов)
def make_chunks_legacy(text: str, size: int = 1600, overlap: int = 150) -> List[str]:
    """
    Старый посимвольный чанкер (только для обратной совместимости).
    """
    text = text.strip()
    if not text:
        return []
    step = max(1, size - overlap)
    chunks = []
    i = 0
    while i < len(text):
        chunk = text[i : i + size]
        chunks.append(chunk)
        i += step
    return chunks