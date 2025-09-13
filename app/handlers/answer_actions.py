from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.utils.media_group import MediaGroupBuilder
from app.models import BotMessage, Artifact
from app.llm import summarize_text
from app.services.memory import get_active_project
from app.services.memory import list_projects as list_all_projects
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.db import session_scope
import asyncio
from typing import cast

router = Router()

def _project_pick_kb(msg_id: int, purpose: str, projects):
    # purpose: "save" | "sum"
    rows = []
    for p in projects:
        rows.append([InlineKeyboardButton(text=p.name, callback_data=f"ans:pickproj:{purpose}:{msg_id}:{p.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data.startswith("ans:save:"))
async def ans_save(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
        
    async with session_scope() as st:
        msg_id = int(cb.data.split(":")[-1])
        from sqlalchemy import select
        stmt = select(BotMessage).where(BotMessage.tg_message_id == msg_id)
        result = await st.execute(stmt)
        bm = result.scalar_one_or_none()
        
        if not bm:
            return await cb.answer("Message not found", show_alert=True)
        if bm.saved and bm.artifact_id:
            return await cb.answer("Уже сохранено")

        # нужен проект
        target_pid = bm.project_id
        if not target_pid:
            proj = await get_active_project(st, cb.from_user.id)
            if proj:
                target_pid = proj.id
            else:
                projects = await list_all_projects(st)
                if not projects:
                    if cb.message and isinstance(cb.message, Message):
                        return await cb.message.answer("Сначала создай проект: Actions → Projects → ➕ New")
                kb = _project_pick_kb(msg_id, "save", projects)
                if cb.message and isinstance(cb.message, Message):
                    await cb.message.answer("Выбери проект для сохранения:", reply_markup=kb)
                return await cb.answer()

        text = ""
        if cb.message and isinstance(cb.message, Message):
            text = cb.message.text or cb.message.caption or ""
        art = Artifact(project_id=target_pid, kind="answer", title="Chat answer", raw_text=text, pinned=False)
        st.add(art)
        await st.flush()
        bm.artifact_id = art.id
        bm.saved = True
        st.add(bm)
        await st.commit()
    await cb.answer("Сохранено ✅")

@router.callback_query(F.data.startswith("ans:sum:"))
async def ans_summary(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
        
    async with session_scope() as st:
        msg_id = int(cb.data.split(":")[-1])
        from sqlalchemy import select
        stmt = select(BotMessage).where(BotMessage.tg_message_id == msg_id)
        result = await st.execute(stmt)
        bm = result.scalar_one_or_none()
        
        if not bm:
            return await cb.answer("Message not found", show_alert=True)

        text = ""
        if cb.message and isinstance(cb.message, Message):
            text = cb.message.text or cb.message.caption or ""

        # Определяем проект для сохранения
        target_pid = bm.project_id
        if not target_pid:
            proj = await get_active_project(st, cb.from_user.id)
            if proj:
                target_pid = proj.id
            else:
                projects = await list_all_projects(st)
                if not projects:
                    if cb.message and isinstance(cb.message, Message):
                        return await cb.message.answer("Сначала создай проект: Actions → Projects → ➕ New")
                kb = _project_pick_kb(msg_id, "sum", projects)
                if cb.message and isinstance(cb.message, Message):
                    await cb.message.answer("Выбери проект для 📌 Summary:", reply_markup=kb)
                return await cb.answer()

        # если не сохранён base — создаём
        if not bm.saved or not bm.artifact_id:
            base = Artifact(project_id=target_pid, kind="answer", title="Chat answer", raw_text=text, pinned=True)
            st.add(base)
            await st.flush()
            bm.artifact_id = base.id
            bm.saved = True
        else:
            base = await st.get(Artifact, bm.artifact_id)
            if base:
                base.pinned = True

        summary = await summarize_text(text)
        summ = Artifact(project_id=target_pid, kind="summary", title="Summary", raw_text=summary, pinned=True, parent_id=bm.artifact_id)
        st.add(summ)
        await st.commit()

    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("📌 Суммаризация сохранена и закреплена.")
    await cb.answer()

# новый обработчик выбора проекта из клавиатуры
@router.callback_query(F.data.startswith("ans:pickproj:"))
async def ans_pickproj(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
        
    parts = cb.data.split(":")
    if len(parts) < 6:
        return await cb.answer("Invalid data format")
        
    _, _, purpose, msg_id_str, pid_str = parts[:5]
    msg_id = int(msg_id_str)
    pid = int(pid_str)
    
    async with session_scope() as st:
        from sqlalchemy import select
        stmt = select(BotMessage).where(BotMessage.tg_message_id == msg_id)
        result = await st.execute(stmt)
        bm = result.scalar_one_or_none()
        
        if not bm:
            return await cb.answer("Message not found", show_alert=True)
        # установим проект в BotMessage (чтобы дальнейшие действия знали исходный проект)
        bm.project_id = pid
        st.add(bm)
        await st.commit()
    # повторно «нажмём» действие
    if purpose == "save":
        await ans_save(cb)
    else:
        await ans_summary(cb)

@router.callback_query(F.data.startswith("ans:del:"))
async def ans_del(cb: CallbackQuery):
    # удаляем СВОЁ сообщение
    try:
        if cb.message and isinstance(cb.message, Message):
            await cb.message.delete()
    except:
        pass
    # аккуратная плашка (самоудалится)
    note = None
    if cb.message and isinstance(cb.message, Message):
        note = await cb.message.answer("🧹 Очищено")
    if note:
        await asyncio.sleep(3)
        try:
            await note.delete()
        except:
            pass
    await cb.answer()