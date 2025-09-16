"""Markdown utility for text escaping."""
import re
from typing import Dict, Any

def escape_markdown_v2(text: str) -> str:
    """
    Escape text for MarkdownV2 formatting in Telegram.
    
    Args:
        text: Text to escape
        
    Returns:
        Escaped text
    """
    if not text:
        return ""
    
    # Characters to escape in MarkdownV2: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    
    # Escape each character
    escaped = text
    for char in escape_chars:
        escaped = escaped.replace(char, '\\' + char)
    
    return escaped

def escape_html(text: str) -> str:
    """
    Escape text for HTML formatting.
    
    Args:
        text: Text to escape
        
    Returns:
        Escaped text
    """
    if not text:
        return ""
    
    html_escape_table = {
        "&": "&amp;",
        '"': "&quot;",
        "'": "&#x27;",
        ">": "&gt;",
        "<": "&lt;",
    }
    
    return "".join(html_escape_table.get(c, c) for c in text)

def safe_json_serialize(obj: Any) -> str:
    """
    Safely serialize object to JSON string.
    
    Args:
        obj: Object to serialize
        
    Returns:
        JSON string representation
    """
    import json
    
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        # Fallback to string representation
        return str(obj)

def format_response_with_sources(response_text: str, sources: list) -> str:
    """
    Format the LLM response with source references.
    
    Args:
        response_text: LLM response text
        sources: List of sources used
        
    Returns:
        Formatted response with source references
    """
    if not response_text:
        return ""
    
    # For now, we'll just return the response text
    # In a more advanced implementation, we could process source references
    return response_text