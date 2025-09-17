"""Retrieval service for loading content from selected sources."""
import logging
import unicodedata
from typing import List, Tuple, Dict, Any
from sqlalchemy import select

from app.db import session_scope
from app.models import Artifact, Chunk
from app.services.memory import get_linked_project_ids

logger = logging.getLogger(__name__)

async def load_selected_sources(user_id: int, selected_artifact_ids: List[int]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Load content from selected sources (active + linked projects).
    
    Returns:
        Tuple of (sources_metadata, total_tokens)
    """
    if not selected_artifact_ids:
        return [], 0
    
    async with session_scope() as session:
        # Get active project and linked project IDs
        from app.services.memory import get_active_project
        active_proj = await get_active_project(session, user_id)
        if not active_proj:
            return [], 0
            
        linked_project_ids = await get_linked_project_ids(session, user_id)
        project_ids = [active_proj.id] + linked_project_ids
        
        # Load artifacts with their metadata
        query = select(Artifact).where(
            Artifact.id.in_(selected_artifact_ids),
            Artifact.project_id.in_(project_ids)
        )
        
        result = await session.execute(query)
        artifacts = result.scalars().all()
        
        sources_metadata = []
        total_tokens = 0
        
        # Process each artifact
        for artifact in artifacts:
            # Load chunks for this artifact
            chunk_query = select(Chunk).where(Chunk.artifact_id == artifact.id).order_by(Chunk.idx)
            chunk_result = await session.execute(chunk_query)
            chunks = chunk_result.scalars().all()
            
            # Normalize text content (UTF-8 + NFC)
            normalized_chunks = []
            artifact_tokens = 0
            
            for chunk in chunks:
                # Normalize text
                normalized_text = normalize_text(chunk.text)
                # Only include chunks with actual content
                if normalized_text.strip():
                    normalized_chunks.append({
                        "idx": chunk.idx,
                        "text": normalized_text,
                        "tokens": chunk.tokens or len(normalized_text) // 4  # Fallback estimation
                    })
                    
                    # Count tokens
                    artifact_tokens += chunk.tokens or len(normalized_text) // 4
            
            # Create source metadata (only if we have content)
            if normalized_chunks:
                source_metadata = {
                    "id": artifact.id,
                    "title": artifact.title or str(artifact.id),
                    "tags": [tag.name for tag in artifact.tags] if artifact.tags else [],
                    "created_at": artifact.created_at,
                    "chunks": normalized_chunks,
                    "total_tokens": artifact_tokens
                }
                
                sources_metadata.append(source_metadata)
                total_tokens += artifact_tokens
        
        # Remove duplicates (by content hash)
        sources_metadata = remove_duplicate_sources(sources_metadata)
        
        return sources_metadata, total_tokens

def normalize_text(text: str) -> str:
    """
    Normalize text to UTF-8 NFC and clean control characters.
    """
    if not text:
        return ""
    
    # Normalize to NFC form
    normalized = unicodedata.normalize('NFC', text)
    
    # Remove null characters and control characters (except newlines and tabs)
    cleaned = ''.join(char for char in normalized if char == '\n' or char == '\t' or not unicodedata.category(char).startswith('C'))
    
    return cleaned

def remove_duplicate_sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove duplicate sources based on content similarity.
    """
    if not sources:
        return sources
    
    # For now, we'll just return all sources
    # In a more advanced implementation, we could check for duplicate content
    return sources

def extract_chunks_for_context(sources: List[Dict[str, Any]], token_budget: int) -> List[Dict[str, Any]]:
    """
    Extract chunks from sources based on token budget.
    
    Returns:
        List of chunks with metadata
    """
    if not sources or token_budget <= 0:
        return []
    
    # Calculate budget per source
    num_sources = len(sources)
    budget_per_source = token_budget // num_sources if num_sources > 0 else token_budget
    
    extracted_chunks = []
    
    # Extract chunks from each source
    for source in sources:
        source_chunks = source.get("chunks", [])
        source_tokens = 0
        
        # Take chunks until we exceed the budget for this source
        for chunk in source_chunks:
            chunk_tokens = chunk.get("tokens", len(chunk.get("text", "")) // 4)
            if source_tokens + chunk_tokens > budget_per_source:
                break
                
            extracted_chunks.append({
                "source_id": source["id"],
                "source_title": source["title"],
                "chunk_idx": chunk["idx"],
                "text": chunk["text"],
                "tokens": chunk_tokens
            })
            
            source_tokens += chunk_tokens
    
    return extracted_chunks