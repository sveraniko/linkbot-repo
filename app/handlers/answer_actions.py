from __future__ import annotations
from aiogram import Router, F
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardMarkup, InlineKeyboardButton, Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.db import get_session
from app.models import BotMessage, Artifact
from app.config import settings
from app.services.memory import get_active_project

router = Router()

# -- SAVE --
@router.callback_query(F.data.startswith("ans:save:"))
async def ans_save(cb: CallbackQuery, session: AsyncSession = get_session()):
    st = await anext(session)
    msg_id = int(cb.data.split(":")[-1])
    bm = await st.scalar(select(BotMessage).where(BotMessage.tg_message_id==msg_id))
    if not bm:
        await cb.answer("Message not found", show_alert=True); return
    if bm.saved and bm.artifact_id:
        await cb.answer("Already saved"); return
    # спросим теги
    await cb.message.answer("Укажи теги для сохранения (через запятую):", reply_markup=ForceReply(selective=True))
    await cb.answer()

# Хэндлер ForceReply для тегов (привяжем по reply_to_message)
@router.message(F.reply_to_message & F.reply_to_message.text.regexp("^Укажи теги"))
async def tags_reply(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    # найдём последнее сообщение бота рядом (упрощённо: возьмём последнее BotMessage по user_id)
    if not message.from_user:
        return
    result = await st.execute(select(BotMessage).where(BotMessage.user_id==message.from_user.id).order_by(BotMessage.created_at.desc()).limit(1))
    bm = result.scalars().first()
    if not bm:
        return await message.answer("Не нашёл сообщение для сохранения.")
    tags = [t.strip() for t in (message.text or "").split(",") if t.strip()]
    proj = await get_active_project(st, message.from_user.id)
    art = Artifact(project_id=bm.project_id or (proj.id if proj else None),
                   kind="answer", title="Chat answer", raw_text="", # безопасная заглушка
                   )
    st.add(art); await st.flush()
    bm.artifact_id = art.id; bm.saved = True
    st.add(bm); await st.commit()
    await message.answer(f"✅ Сохранено (artifact #{art.id}).")

# -- SUMMARY (обязательно) --
@router.callback_query(F.data.startswith("ans:sum:"))
async def ans_summary(cb: CallbackQuery, session: AsyncSession = get_session()):
    st = await anext(session)
    msg_id = int(cb.data.split(":")[-1])
    result = await st.execute(select(BotMessage).where(BotMessage.tg_message_id==msg_id))
    bm = result.scalars().first()
    if not bm:
        await cb.answer("Message not found", show_alert=True); return
    # получим текст исходного сообщения бота
    text = cb.message.text or cb.message.caption or ""
    # если ещё не сохранён — сохраним как answer
    if not bm.saved or not bm.artifact_id:
        base = Artifact(project_id=bm.project_id, kind="answer", title="Chat answer", raw_text=text, pinned=True)
        st.add(base); await st.flush()
        bm.artifact_id = base.id; bm.saved = True
    else:
        base = await st.get(Artifact, bm.artifact_id)
        if base:
            base.pinned = True
    # создаём summary
    from app.llm import ask_llm
    summary = await ask_llm(f"Summarize this text: {text}", [], model="gpt-5")  # внутри llm выбери модель сам (или возьми preferred)
    summ = Artifact(project_id=bm.project_id, kind="summary", title="Summary", raw_text=summary, pinned=True, parent_id=bm.artifact_id)
    st.add(summ); await st.commit()
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.answer("📌 Суммаризация добавлена и закреплена.")

# -- TAG --
@router.callback_query(F.data.startswith("ans:tag:"))
async def ans_tag(cb: CallbackQuery, session: AsyncSession = get_session()):
    st = await anext(session)
    msg_id = int(cb.data.split(":")[-1])
    result = await st.execute(select(BotMessage).where(BotMessage.tg_message_id==msg_id))
    bm = result.scalars().first()
    if not bm or not bm.artifact_id:
        await cb.answer("Сначала сохраните ответ (Save).", show_alert=True); return
    await cb.message.answer("Новые теги (через запятую):", reply_markup=ForceReply(selective=True))
    await cb.answer()

# -- DELETE --
@router.callback_query(F.data.startswith("ans:del:"))
async def ans_del(cb: CallbackQuery, session: AsyncSession = get_session()):
    st = await anext(session)
    msg_id = int(cb.data.split(":")[-1])
    result = await st.execute(select(BotMessage).where(BotMessage.tg_message_id==msg_id))
    bm = result.scalars().first()
    if not bm:
        await cb.answer("Message not found", show_alert=True); return
    if bm.saved and bm.artifact_id:
        # спросим подтверждение
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Удалить из памяти и чата", callback_data=f"ans:delmem:{msg_id}:yes"),
            InlineKeyboardButton(text="Только из чата", callback_data=f"ans:delmem:{msg_id}:no"),
        ]])
        await cb.message.answer("Этот ответ уже сохранён. Как удалить?", reply_markup=kb)
        await cb.answer(); return
    # просто удалим сообщение бота
    try:
        await cb.message.delete()
    except:
        pass
    await cb.answer("Удалено")

@router.callback_query(F.data.startswith("ans:delmem:"))
async def ans_delmem(cb: CallbackQuery, session: AsyncSession = get_session()):
    st = await anext(session)
    _, _, msg_id, choice = cb.data.split(":")
    msg_id = int(msg_id)
    result = await st.execute(select(BotMessage).where(BotMessage.tg_message_id==msg_id))
    bm = result.scalars().first()
    if not bm:
        return await cb.answer("Not found")
    if choice == "yes" and bm.artifact_id:
        await st.execute(delete(Artifact).where(Artifact.id==bm.artifact_id))
        await st.commit()
    try:
        await cb.message.delete()
    except:
        pass
    await cb.answer("Готово")

# -- REFINE (упрощённо: спросим уточнение, затем создадим новый ответ на основе старого)
@router.callback_query(F.data.startswith("ans:ref:"))
async def ans_ref(cb: CallbackQuery, session: AsyncSession = get_session()):
    await cb.message.answer("Что уточнить?", reply_markup=ForceReply(selective=True))
    await cb.answer()