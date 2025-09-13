"""Token-based chunking with tiktoken + graceful fallback."""
from __future__ import annotations
from typing import List

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:
    tiktoken = None
    _enc = None

def _encode(text: str) -> List[int]:
    """Encode text to tokens, with fallback to bytes if tiktoken unavailable."""
    if _enc is None:
        return list(text.encode("utf-8", errors="ignore"))
    return _enc.encode(text)

def _decode(tokens: List[int]) -> str:
    """Decode tokens to text, with fallback to bytes if tiktoken unavailable."""
    if _enc is None:
        return bytes(tokens).decode("utf-8", errors="ignore")
    return _enc.decode(tokens)

def make_chunks(text: str, size: int = 1600, overlap: int = 150) -> list[str]:
    """
    Создает чанки текста на основе токенов с грациозным откатом.
    """
    text = text.strip()
    if not text:
        return []
    
    toks = _encode(text)
    step = max(1, size - max(0, overlap))
    chunks: list[str] = []
    
    for i in range(0, len(toks), step):
        window = toks[i:i + size]
        chunks.append(_decode(window))
    
    return chunks

def count_tokens(text: str) -> int:
    """
    Подсчитывает количество токенов в тексте.
    """
    return len(_encode(text))