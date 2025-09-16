"""Prompt builder service for assembling system/context/user blocks."""
import logging
from typing import List, Dict, Any
from html import escape

logger = logging.getLogger(__name__)

def build_system_prompt() -> str:
    """
    Build the system prompt with the bot's role and instructions.
    """
    return """You are a helpful AI assistant integrated into a project memory management system. 
Your role is to help users analyze and understand their project documentation, chat logs, and other 
collected information.

Instructions:
1. Always reference the provided sources when answering questions
2. Do not make up information that is not in the sources
3. If you don't have enough information to answer a question, say so
4. Provide concise, accurate answers
5. When referencing sources, use the format [source_id] where source_id is the numeric ID
6. Answer in the same language the user is using
7. Be helpful but do not be verbose

The user has selected specific sources from their projects which will be provided in the context."""

def build_context_prompt(sources: List[Dict[str, Any]]) -> str:
    """
    Build the context prompt with selected sources.
    
    Args:
        sources: List of source metadata with chunks
        
    Returns:
        Formatted context prompt
    """
    if not sources:
        return "No sources selected."
    
    context_parts = ["SOURCES:"]
    
    for source in sources:
        source_id = source["id"]
        title = source["title"]
        tags = source.get("tags", [])
        chunks = source.get("chunks", [])
        
        # Add source header
        tag_str = " ".join([f"#{tag}" for tag in tags]) if tags else ""
        context_parts.append(f"\nSOURCE [{source_id}] - {title} {tag_str}")
        
        # Add chunks
        for chunk in chunks:
            chunk_text = chunk.get("text", "")
            if chunk_text.strip():
                context_parts.append(f"- {chunk_text}")
    
    return "\n".join(context_parts)

def build_user_prompt(question: str) -> str:
    """
    Build the user prompt with the question, applying proper escaping.
    
    Args:
        question: User's question
        
    Returns:
        Escaped user prompt
    """
    # Escape HTML characters to prevent injection
    escaped_question = escape(question)
    return escaped_question

def format_source_chips(sources: List[Dict[str, Any]]) -> str:
    """
    Format source chips for display at the bottom of the response.
    
    Args:
        sources: List of source metadata
        
    Returns:
        Formatted source chips string
    """
    if not sources:
        return ""
    
    chips = []
    for source in sources:
        source_id = source["id"]
        title = source["title"]
        # Create a short tag-like representation
        short_title = title[:20] + "..." if len(title) > 20 else title
        chips.append(f"[#{short_title} id{source_id}]")
    
    return f"\n\n📚 Sources: {', '.join(chips)}"