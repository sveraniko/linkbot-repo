from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Artifact, Project
import datetime as dt
import io
import zipfile
from html import escape

async def export_project_zip(st: AsyncSession, project: Project, kinds: list[str] | None = None, tags: list[str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # markdown-dамп всего
        md = ["# Export", f"Project: {escape(project.name)}", f"Date: {dt.datetime.utcnow().isoformat()}Z", ""]
        q = select(Artifact).where(Artifact.project_id == project.id).order_by(Artifact.created_at.asc())
        if kinds:
            q = q.where(Artifact.kind.in_(kinds))
        res = await st.execute(q)
        for a in res.scalars():
            md.append(f"## [{escape(a.kind)}] {escape(a.title)}  \n<small>{a.created_at}</small>\n\n```\n{escape(a.raw_text)}\n```")
        z.writestr("EXPORT.md", "\n".join(md))
    buf.seek(0)
    return buf.getvalue()