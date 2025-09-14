from __future__ import annotations
import re, datetime as dt
import sqlalchemy as sa
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from app.db import session_scope
from app.ui import show_panel
from app.services.memory import get_active_project
from app.models import Artifact, artifact_tags

router = Router()

def cleanup_menu():
    rows = [[
        InlineKeyboardButton(text="🗓 By date", callback_data="cleanup:bydate"),
        InlineKeyboardButton(text="🏷 By tag", callback_data="cleanup:bytag"),
    ]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data == "cleanup:open")
async def cleanup_open(cb: CallbackQuery):
    if cb.message and isinstance(cb.message, Message) and cb.message.bot and cb.from_user:
        async with session_scope() as st:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                             "Cleanup — выбери режим:", cleanup_menu())
    await cb.answer()

# --- BY DATE ---
@router.callback_query(F.data == "cleanup:bydate")
async def cleanup_bydate(cb: CallbackQuery):
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(
            "Введи дату в формате YYYY-MM-DD (удалим с ЭТОЙ даты и позже):",
            reply_markup=ForceReply(selective=True)
        )
    await cb.answer()

@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Введи дату в формате"))
async def cleanup_date_reply(message: Message):
    from app.ui import show_panel
    if not message.bot:
        return
    m = re.search(r"(\d{4}-\d{2}-\d{2})", message.text or "")
    if not m:
        return await message.answer("Формат неверный. Пример: 2025-09-13")
    d0 = dt.date.fromisoformat(m.group(1))
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id if message.from_user else 0)
        if not proj:
            return await message.answer("Сначала выбери проект (Actions → Projects).")
        since = dt.datetime.combine(d0, dt.time.min)
        cnt = (await st.execute(sa.select(sa.func.count()).select_from(Artifact)
               .where(Artifact.project_id == proj.id, Artifact.created_at >= since))).scalar_one()
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Delete {cnt}", callback_data=f"cleanup:confirm:date:{d0.isoformat()}"),
            InlineKeyboardButton(text="✖ Cancel", callback_data="cleanup:cancel")
        ]])
        # ВАЖНО: панель через show_panel
        await show_panel(st, message.bot, message.chat.id, message.from_user.id if message.from_user else 0,
                         f"Найдено артефактов: {cnt}. Удалить?", kb)

@router.callback_query(F.data.startswith("cleanup:confirm:date:"))
async def cleanup_confirm_date(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    from app.ui import clear_panel
    if not cb.data:
        return await cb.answer("Invalid data")
    if not cb.message or not cb.message.bot:
        return await cb.answer("Invalid message")
    d = cb.data.split(":")[-1]
    since = dt.datetime.fromisoformat(d)
    async with session_scope() as st:
        proj = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
        if not proj:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            if cb.message and isinstance(cb.message, Message):
                return await cb.message.answer("Нет активного проекта", reply_markup=build_reply_kb(chat_on))
            return await cb.answer("Нет активного проекта", show_alert=True)
        res = await st.execute(sa.delete(Artifact).where(Artifact.project_id == proj.id,
                                                        Artifact.created_at >= since))
        await st.commit()
        # Снесём панель подтверждения:
        await clear_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id if cb.from_user else 0)
    if cb.message and isinstance(cb.message, Message):
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        await cb.message.answer(f"🧹 Удалено (с {d}): {res.rowcount or 0}", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

# --- BY TAG ---
@router.callback_query(F.data == "cleanup:bytag")
async def cleanup_bytag(cb: CallbackQuery):
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(
            "Введи тег (точное совпадение) или префикс с * в конце (например: rel-2025-09-*)",
            reply_markup=ForceReply(selective=True)
        )
    await cb.answer()

@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Введи тег (точное совпадение)"))
async def cleanup_tag_reply(message: Message):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    from app.ui import show_panel
    if not message.bot:
        return
    pattern = (message.text or "").strip()
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id if message.from_user else 0)
        if not proj:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            return await message.answer("Сначала выбери проект (Actions → Projects).", reply_markup=build_reply_kb(chat_on))
        if pattern.endswith("*"):
            like = pattern[:-1] + "%"
            sel = sa.select(artifact_tags.c.artifact_id).where(artifact_tags.c.tag_name.like(like))
        else:
            sel = sa.select(artifact_tags.c.artifact_id).where(artifact_tags.c.tag_name == pattern)
        ids = [r[0] for r in (await st.execute(sel)).fetchall()]
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Delete {len(ids)}", callback_data=f"cleanup:confirm:tag"),
            InlineKeyboardButton(text="✖ Cancel", callback_data="cleanup:cancel")
        ]])
        # сохраним список id в «простом кеше» (ключ = user_id)
        from app.services.memory import _ensure_user_state
        stt = await _ensure_user_state(st, message.from_user.id if message.from_user else 0)
        # hack: положим в context_tags поле временно
        stt.context_tags = ",".join(str(i) for i in ids)
        await st.commit()
        # ВАЖНО: панель через show_panel
        await show_panel(st, message.bot, message.chat.id, message.from_user.id if message.from_user else 0,
                         f"Под тег {pattern} подходит артефактов: {len(ids)}. Удалить?", kb)

@router.callback_query(F.data == "cleanup:confirm:tag")
async def cleanup_confirm_tag(cb: CallbackQuery):
    from app.services.memory import _ensure_user_state
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    from app.ui import clear_panel
    if not cb.message or not cb.message.bot:
        return await cb.answer("Invalid message")
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id if cb.from_user else 0)
        ids = [int(x) for x in (stt.context_tags or "").split(",") if x.strip().isdigit()]
        stt.context_tags = None
        if not ids:
            await st.commit()
            return await cb.answer("Нечего удалять", show_alert=True)
        res = await st.execute(sa.delete(Artifact).where(Artifact.id.in_(ids)))
        await st.commit()
        # Снесём панель подтверждения:
        await clear_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id if cb.from_user else 0)
    if cb.message and isinstance(cb.message, Message):
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        await cb.message.answer(f"🧹 Удалено по тегу: {res.rowcount or 0}", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

@router.callback_query(F.data == "cleanup:cancel")
async def cleanup_cancel(cb: CallbackQuery):
    from app.ui import clear_panel
    if not cb.message or not cb.message.bot:
        return await cb.answer("Invalid message")
    async with session_scope() as st:
        await clear_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id if cb.from_user else 0)
    await cb.answer("Отменено")