"""ASK list/search/pagination service (Telegram-agnostic)
Contracts:
- parse_query(term) -> (mode, value)
- list_sources(project_ids, term=None, page=1, page_size=5) -> (items, total)
"""
from __future__ import annotations
import sqlalchemy as sa
from typing import Tuple, List, Optional
from app.db import session_scope
from app.models import Artifact, Tag, artifact_tags

PAGE_SIZE_DEFAULT = 5

def parse_query(term: Optional[str]) -> Tuple[str, Optional[str]]:
    if not term:
        return ("all", None)
    term = term.strip()
    if term.isdigit():
        return ("id", term)
    if term.startswith("#") and len(term) > 1:
        return ("tag", term[1:])
    return ("name", term)

async def list_sources(
    project_ids: List[int],
    term: Optional[str] = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
) -> Tuple[List[Artifact], int]:
    mode, value = parse_query(term)
    async with session_scope() as st:
        # subquery with all filters
        subq = sa.select(Artifact.id).where(Artifact.project_id.in_(project_ids))
        if mode == "id" and value:
            try:
                aid = int(value)
            except ValueError:
                aid = -1
            subq = subq.where(Artifact.id == aid)
        elif mode == "tag" and value:
            tag_subq = (
                sa.select(sa.distinct(artifact_tags.c.artifact_id))
                .join(Tag, artifact_tags.c.tag_name == Tag.name)
                .where(sa.func.lower(Tag.name).like(f"%{value.lower()}%"))
                .where(artifact_tags.c.artifact_id == Artifact.id)
            )
            subq = subq.where(sa.exists(tag_subq))
        elif mode == "name" and value:
            subq = subq.where(sa.func.lower(Artifact.title).like(f"%{value.lower()}%"))
        subq = subq.distinct()
        # main query
        base = sa.select(Artifact).where(Artifact.id.in_(subq)).order_by(Artifact.created_at.desc())
        total = (
            await st.execute(
                sa.select(sa.func.count(Artifact.id)).where(Artifact.id.in_(subq))
            )
        ).scalar_one() or 0
        items = (
            await st.execute(base.limit(page_size).offset((page - 1) * page_size))
        ).scalars().all()
        # python-level unique safety
        uniq, seen = [], set()
        for a in items:
            if a.id not in seen:
                uniq.append(a); seen.add(a.id)
        return uniq, int(total)
