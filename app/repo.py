from __future__ import annotations
import subprocess, os, shlex, datetime as dt
from pathlib import Path
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from app.models import Repo

BASE = Path("/app/repos")

def _runcmd(cmd: str, cwd: Optional[Path] = None) -> tuple[int, str]:
    p = subprocess.Popen(shlex.split(cmd), cwd=str(cwd) if cwd else None,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out, _ = p.communicate()
    return p.returncode, out

async def repo_add(st: AsyncSession, user_id: int, alias: str, url: str, branch: str = "main"):
    r = Repo(user_id=user_id, alias=alias, url=url, branch=branch)
    st.add(r)
    await st.flush()
    return r

async def repo_list(st: AsyncSession, user_id: int):
    res = await st.execute(select(Repo).where(Repo.user_id==user_id).order_by(Repo.alias.asc()))
    return list(res.scalars())

async def repo_remove(st: AsyncSession, user_id: int, alias: str):
    await st.execute(delete(Repo).where(Repo.user_id==user_id, Repo.alias==alias))

async def repo_sync(st: AsyncSession, user_id: int, alias: str, token: str | None = None) -> str:
    res = await st.execute(select(Repo).where(Repo.user_id==user_id, Repo.alias==alias))
    r = res.scalars().first()
    if not r: return "Repo not found."
    BASE.mkdir(parents=True, exist_ok=True)
    path = BASE / alias
    # URL с токеном для приватных реп
    url = r.url
    if token and url.startswith("https://github.com/"):
        url = url.replace("https://", f"https://{token}@", 1)

    if not path.exists():
        code, out = _runcmd(f"git clone --depth=1 --branch {r.branch} {shlex.quote(url)} {shlex.quote(str(path))}")
    else:
        code, out = _runcmd("git fetch --all", cwd=path)
        if code==0:
            code, out2 = _runcmd(f"git reset --hard origin/{r.branch}", cwd=path)
            out += "\n" + out2
    if code==0:
        # Update the last_synced_at field using an update statement
        await st.execute(
            update(Repo)
            .where(Repo.id == r.id)
            .values(last_synced_at=dt.datetime.now(dt.timezone.utc))
        )
    await st.flush()
    return out