"""ASK selection service (Telegram-agnostic)
Contracts:
- toggle_selection(session, user_id: int, artifact_id: int) -> added: bool
- clear_selection(session, user_id: int) -> None
- set_autoclear(session, user_id: int, on: bool) -> bool
- get_selection(session, user_id: int) -> list[int]
"""
from __future__ import annotations
from typing import Iterable, List
import sqlalchemy as sa
from app.db import session_scope
from app.models import UserState


def _ids_get(stt: UserState) -> list[int]:
    raw = (stt.selected_artifact_ids or "").strip()
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


def _ids_set(stt: UserState, ids: Iterable[int]) -> None:
    uniq = sorted({int(i) for i in ids})
    stt.selected_artifact_ids = ",".join(str(i) for i in uniq) if uniq else None


async def get_selection(session, user_id: int) -> List[int]:
    stt = await session.get(UserState, user_id)
    if not stt:
        stt = UserState(user_id=user_id)
        session.add(stt)
        await session.flush()
    return _ids_get(stt)


async def toggle_selection(session, user_id: int, artifact_id: int) -> bool:
    stt = await session.get(UserState, user_id)
    if not stt:
        stt = UserState(user_id=user_id)
        session.add(stt)
        await session.flush()
    current = set(_ids_get(stt))
    added = False
    if artifact_id in current:
        current.remove(artifact_id)
    else:
        current.add(artifact_id)
        added = True
    _ids_set(stt, current)
    await session.flush()
    return added


async def clear_selection(session, user_id: int) -> None:
    stt = await session.get(UserState, user_id)
    if not stt:
        stt = UserState(user_id=user_id)
        session.add(stt)
        await session.flush()
    _ids_set(stt, [])
    await session.flush()


async def set_autoclear(session, user_id: int, on: bool) -> bool:
    stt = await session.get(UserState, user_id)
    if not stt:
        stt = UserState(user_id=user_id)
        session.add(stt)
        await session.flush()
    stt.auto_clear_selection = bool(on)
    await session.flush()
    return stt.auto_clear_selection
