from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, insert
from app.models import Project
from sqlalchemy import Table, Column, Integer, BigInteger, String, ForeignKey, MetaData

# быстрая табличка через Core (или добавь ORM-модель)
metadata = MetaData()
tag_presets = Table(
    "tag_presets", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", BigInteger, nullable=False),
    Column("project_id", Integer, nullable=True),
    Column("tag", String(64), nullable=False),
)

DEFAULT_PRESETS = ["api","db","infra","matching","ui","auth","spec","plan","answer","summary","pinned","release-notes"]

async def get_presets(st: AsyncSession, user_id: int, project_id: int | None):
    res = await st.execute(select(tag_presets.c.tag).where(tag_presets.c.user_id==user_id, tag_presets.c.project_id==project_id))
    tags = [r[0] for r in res.fetchall()]
    return tags or DEFAULT_PRESETS

async def add_preset(st: AsyncSession, user_id: int, project_id: int | None, tag: str):
    await st.execute(insert(tag_presets).values(user_id=user_id, project_id=project_id, tag=tag.strip().lower()))

async def clear_presets(st: AsyncSession, user_id: int, project_id: int | None):
    await st.execute(delete(tag_presets).where(tag_presets.c.user_id==user_id, tag_presets.c.project_id==project_id))