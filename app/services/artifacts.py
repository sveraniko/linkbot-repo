from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Project, Artifact, Chunk, Tag
from app.tokenizer import make_chunks, count_tokens
from typing import Optional, List

async def get_chunks_by_artifact_ids(session: AsyncSession, artifact_ids: list[int], limit: int = 200) -> list[str]:
    """Get text chunks for specific artifact IDs."""
    from sqlalchemy import select
    from app.models import Chunk
    
    # Get chunks for the specified artifacts, ordered by artifact_id and chunk index
    stmt = select(Chunk).where(Chunk.artifact_id.in_(artifact_ids)).order_by(Chunk.artifact_id, Chunk.idx).limit(limit)
    result = await session.execute(stmt)
    chunks = result.scalars().all()
    
    # Return just the text content
    return [chunk.text for chunk in chunks]


async def approx_tokens_for_selection(session: AsyncSession, artifact_ids: list[int], model: str) -> int:
    """Approximate token count for selected artifacts."""
    # Get chunks for the specified artifacts
    chunks = await get_chunks_by_artifact_ids(session, artifact_ids, limit=2000)
    
    # Calculate approximate tokens
    toks = 0
    for text in chunks:
        # If chunk has tokens field, use it; otherwise estimate as len(text)/4
        toks += max(1, len(text) // 4)
    return toks


async def get_or_create_project(session: AsyncSession, name: str) -> Project:
    res = await session.execute(select(Project).where(Project.name == name))
    proj = res.scalar_one_or_none()
    if not proj:
        proj = Project(name=name)
        session.add(proj)
        await session.flush()
    return proj

async def _ensure_tags(session: AsyncSession, tag_names: list[str]) -> list[Tag]:
    """Ensure tags exist and return Tag entities."""
    out: list[Tag] = []
    for name in {t.strip() for t in tag_names if t.strip()}:
        t = await session.get(Tag, name)
        if not t:
            t = Tag(name=name)
            session.add(t)
            await session.flush()
        out.append(t)
    return out

async def create_note(session: AsyncSession, project: Project, title: str, text: str, chunk_size: int, overlap: int, tags: Optional[List[str]] = None):
    art = Artifact(project_id=project.id, kind="note", title=title, raw_text=text)
    if tags:
        art.tags = await _ensure_tags(session, tags)
    session.add(art)
    await session.flush()
    
    chunks = make_chunks(text, chunk_size, overlap)
    for idx, ch in enumerate(chunks):
        token_count = count_tokens(ch)
        session.add(Chunk(artifact_id=art.id, idx=idx, text=ch, tokens=token_count))
    return art

async def create_import(session: AsyncSession, project: Project, title: str, text: str, chunk_size: int, overlap: int, tags: Optional[List[str]] = None, uri: Optional[str] = None):
    from sqlalchemy import insert
    from app.models import Artifact, Chunk, artifact_tags
    
    art = Artifact(project_id=project.id, kind="import", title=title, raw_text=text)
    if uri:
        art.uri = uri
    session.add(art)
    await session.flush()
    
    # ✅ Запись тегов
    if tags:
        # Ensure tags exist in the tags table first
        tag_entities = await _ensure_tags(session, tags)
        # Then create the associations in artifact_tags
        rows = [{"artifact_id": art.id, "tag_name": t.name}
                for t in tag_entities]
        if rows:
            await session.execute(insert(artifact_tags).values(rows))
    
    # дальше — чанки
    chunks = make_chunks(text, chunk_size, overlap)
    for idx, ch in enumerate(chunks):
        token_count = count_tokens(ch)
        session.add(Chunk(artifact_id=art.id, idx=idx, text=ch, tokens=token_count))
    return art
