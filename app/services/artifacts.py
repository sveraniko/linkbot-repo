from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Project, Artifact, Chunk, Tag
from app.tokenizer import make_chunks, count_tokens
from typing import Optional, List

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
    art = Artifact(project_id=project.id, kind="import", title=title, raw_text=text)
    if tags:
        art.tags = await _ensure_tags(session, tags)
    if uri:
        art.uri = uri
    session.add(art)
    await session.flush()
    
    chunks = make_chunks(text, chunk_size, overlap)
    for idx, ch in enumerate(chunks):
        token_count = count_tokens(ch)
        session.add(Chunk(artifact_id=art.id, idx=idx, text=ch, tokens=token_count))
    return art