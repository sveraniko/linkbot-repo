from __future__ import annotations
import io, zipfile, datetime as dt
from typing import Iterable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models import Artifact, Project

async def export_project_zip(st: AsyncSession, project: Project, kinds: list[str] | None = None, tags: list[str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # markdown-dамп всего
        md = ["# Export", f"Project: {project.name}", f"Date: {dt.datetime.utcnow().isoformat()}Z", ""]
        q = select(Artifact).where(Artifact.project_id == project.id).order_by(Artifact.created_at.asc())
        if kinds:
            q = q.where(Artifact.kind.in_(kinds))
        res = await st.execute(q)
        for a in res.scalars():
            md.append(f"## [{a.kind}] {a.title}  \n<small>{a.created_at}</small>\n\n```\n{a.raw_text}\n```")
        z.writestr("EXPORT.md", "\n".join(md))
    buf.seek(0)
    return buf.getvalue()