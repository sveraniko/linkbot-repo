from __future__ import annotations
import re, datetime as dt
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, ForceReply, InlineKeyboardMarkup, InlineKeyboardButton
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import session_scope
from app.services.memory import get_active_project
from app.models import Artifact

router = Router()

@router.callback_query(F.data == "cleanup:open")
async def cleanup_open(cb: CallbackQuery):
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(
            "Удалить артефакты ПО ДАТЕ.\n"
            "Введи дату в формате YYYY-MM-DD (удалим с этой даты и позже).",
            reply_markup=ForceReply(selective=True)
        )
    await cb.answer()

@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Удалить артефакты ПО ДАТЕ"))
async def cleanup_reply(message: Message):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", message.text or "")
    if not m:
        return await message.answer("Формат неверный. Пример: 2025-09-13")
    try:
        d0 = dt.date.fromisoformat(m.group(1))
    except:
        return await message.answer("Дата неверная.")
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id if message.from_user else 0)
        if not proj:
            return await message.answer("Сначала выбери проект (Actions → Projects).")
        # удалим всё по created_at >= d0 или имеющее тег rel-d0/позже — выберем по дате создания:
        q = sa.delete(Artifact).where(
            Artifact.project_id == proj.id,
            Artifact.created_at >= dt.datetime.combine(d0, dt.time.min)
        )
        res = await st.execute(q)
        await st.commit()
    await message.answer(f"🧹 Удалено артефактов: {res.rowcount or 0}")