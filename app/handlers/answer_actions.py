from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, ForceReply
from aiogram.utils.media_group import MediaGroupBuilder
from app.models import BotMessage, Artifact, artifact_tags, Tag
from app.llm import summarize_text
from app.services.memory import get_active_project
from app.services.memory import list_projects as list_all_projects
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.db import session_scope
import asyncio
from typing import cast
import sqlalchemy as sa
# Import the new tags service
from app.services.tags import get_presets

router = Router()

# текущее выбранное множество держим в памяти на 2 минуты (простая мапа)
TAG_CACHE: dict[int, set[str]] = {}

def _project_pick_kb(msg_id: int, purpose: str, projects):
    # purpose: "save" | "sum"
    rows = []
    for p in projects:
        rows.append([InlineKeyboardButton(text=p.name, callback_data=f"ans:pickproj:{purpose}:{msg_id}:{p.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_tag_kb(tags: list[str], msg_id: int):
    # 3 в ряд, плюс Done / Free input
    rows, row = [], []
    for i,t in enumerate(tags,1):
        row.append(InlineKeyboardButton(text=t, callback_data=f"ans:tagtoggle:{msg_id}:{t}"))
        if i%3==0: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"ans:tagdone:{msg_id}"),
                 InlineKeyboardButton(text="✍️ Свои", callback_data=f"ans:tagfree:{msg_id}")])
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
            proj = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
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
        
    # После сохранения предлагаем добавить теги
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Теги для этого ответа (через запятую):", reply_markup=ForceReply(selective=True))
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
            proj = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
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

# --- TAG: запросить теги через ForceReply ---
# Заменяем старый обработчик на новый с пресетами
@router.callback_query(F.data.startswith("ans:tag:"))
async def ans_tag(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    msg_id = int(cb.data.split(":")[-1])
    async with session_scope() as st:
        # project-specific пресеты
        bm = (await st.execute(sa.select(BotMessage).where(BotMessage.tg_message_id==msg_id))).scalars().first()
        pid = bm.project_id if bm else None
        presets = await get_presets(st, cb.from_user.id if cb.from_user else 0, pid)
    TAG_CACHE[msg_id] = set()
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Выбери теги (тап по кнопкам), потом нажми «Готово».",
                                reply_markup=build_tag_kb(presets, msg_id))
    await cb.answer()

@router.callback_query(F.data.startswith("ans:tagtoggle:"))
async def ans_tag_toggle(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    _, _, msg_id, tag = cb.data.split(":", 3)
    msg_id = int(msg_id)
    cur = TAG_CACHE.get(msg_id, set())
    if tag in cur: cur.remove(tag)
    else: cur.add(tag)
    TAG_CACHE[msg_id] = cur
    await cb.answer(f"{'+' if tag in cur else '-'} {tag}")

@router.callback_query(F.data.startswith("ans:tagdone:"))
async def ans_tag_done(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    msg_id = int(cb.data.split(":")[-1])
    tags = sorted(TAG_CACHE.get(msg_id, set()))
    if not tags:
        return await cb.answer("Не выбрано ни одного тега.", show_alert=True)
    # применим как раньше, только без ForceReply
    async with session_scope() as st:
        bm = (await st.execute(sa.select(BotMessage).where(BotMessage.tg_message_id==msg_id))).scalars().first()
        if not bm: return await cb.answer("Not found", show_alert=True)
        text = ""
        if cb.message and isinstance(cb.message, Message):
            text = cb.message.text or cb.message.caption or ""
        target_pid = bm.project_id
        if not bm.saved or not bm.artifact_id:
            if not target_pid:
                if cb.message and isinstance(cb.message, Message):
                    return await cb.message.answer("Сначала выбери проект в Actions → Projects.")
                proj = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
                if proj:
                    target_pid = proj.id
                else:
                    if cb.message and isinstance(cb.message, Message):
                        return await cb.message.answer("Сначала выбери проект в Actions → Projects.")
            base = Artifact(project_id=target_pid, kind="answer", title="Chat answer", raw_text=text, pinned=False)
            st.add(base)
            await st.flush()
            bm.artifact_id = base.id
            bm.saved = True
            st.add(bm)
        await st.execute(sa.delete(artifact_tags).where(artifact_tags.c.artifact_id == bm.artifact_id))
        await st.execute(sa.insert(artifact_tags), [{"artifact_id": bm.artifact_id, "tag": t} for t in tags])
        await st.commit()
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"🏷 Теги: {', '.join(tags)}")
    TAG_CACHE.pop(msg_id, None)
    await cb.answer()

@router.callback_query(F.data.startswith("ans:tagfree:"))
async def ans_tag_free(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    msg_id = int(cb.data.split(":")[-1])
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Свои теги (через запятую):", reply_markup=ForceReply(selective=True))
    await cb.answer()

# --- Обработчик ответа с тегами ---
@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Свои теги"))
async def tags_free_reply(message: Message):
    tags = [t.strip() for t in (message.text or "").split(",") if t.strip()]
    if not tags: 
        return await message.answer("Пусто.")
    # Применим к последнему BotMessage пользователя, аналогично предыдущему коду
    async with session_scope() as st:
        bm = (await st.execute(sa.select(BotMessage).where(BotMessage.user_id==(message.from_user.id if message.from_user else 0))
              .order_by(BotMessage.created_at.desc()).limit(1))).scalars().first()
        if not bm: 
            return await message.answer("Не нашёл сообщение для тегов.")
        text = message.reply_to_message and (message.reply_to_message.text or "") or ""
        if message.reply_to_message and message.reply_to_message.caption:
            text = message.reply_to_message.caption
        target_pid = bm.project_id
        if not bm.saved or not bm.artifact_id:
            if not target_pid: 
                return await message.answer("Сначала выбери проект.")
            proj = await get_active_project(st, message.from_user.id if message.from_user else 0)
            if proj:
                target_pid = proj.id
            else:
                return await message.answer("Сначала выбери проект.")
            base = Artifact(project_id=target_pid, kind="answer", title="Chat answer", raw_text=text, pinned=False)
            st.add(base)
            await st.flush()
            bm.artifact_id = base.id
            bm.saved = True
            st.add(bm)
        await st.execute(sa.delete(artifact_tags).where(artifact_tags.c.artifact_id == bm.artifact_id))
        await st.execute(sa.insert(artifact_tags), [{"artifact_id": bm.artifact_id, "tag": t} for t in tags])
        await st.commit()
    await message.answer(f"🏷 Теги: {', '.join(tags)}")

@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Теги для этого ответа"))
async def tags_apply(message: Message):
    tags_raw = (message.text or "")
    new_tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    if not new_tags:
        return await message.answer("Пустые теги. Введи через запятую, например: api,db,infra")

    async with session_scope() as st:
        # Найдём последний BotMessage этого пользователя (MVP-способ)
        stmt = sa.select(BotMessage).where(BotMessage.user_id == (message.from_user.id if message.from_user else 0)).order_by(BotMessage.created_at.desc()).limit(1)
        result = await st.execute(stmt)
        bm = result.scalar_one_or_none()
        
        if not bm:
            return await message.answer("Не нашёл сообщение для тегов. Попробуйте сохранить сообщение сначала.")
            
        # Убедимся, что артефакт существует
        if not bm.artifact_id:
            # Создаём артефакт, если его нет
            text = ""
            if message.reply_to_message:
                text = message.reply_to_message.text or message.reply_to_message.caption or ""
            proj = await get_active_project(st, message.from_user.id if message.from_user else 0)
            if not proj:
                return await message.answer("Сначала выберите проект.")
                
            art = Artifact(
                project_id=proj.id,
                kind="answer",
                title="Chat answer",
                raw_text=text,
                pinned=False
            )
            st.add(art)
            await st.flush()
            bm.artifact_id = art.id
            bm.saved = True
            st.add(bm)
            
        # Удаляем старые теги
        del_stmt = sa.delete(artifact_tags).where(artifact_tags.c.artifact_id == bm.artifact_id)
        await st.execute(del_stmt)
        
        # Добавляем новые теги
        tag_objects = []
        for tag_name in new_tags:
            # Проверяем, существует ли тег
            tag_stmt = sa.select(Tag).where(Tag.name == tag_name)
            tag_result = await st.execute(tag_stmt)
            tag = tag_result.scalar_one_or_none()
            
            if not tag:
                # Создаём новый тег, если его нет
                tag = Tag(name=tag_name)
                st.add(tag)
                await st.flush()
                
            tag_objects.append(tag)
            
        # Связываем теги с артефактом
        insert_data = [{"artifact_id": bm.artifact_id, "tag_name": tag.name} for tag in tag_objects]
        if insert_data:
            insert_stmt = sa.insert(artifact_tags).values(insert_data)
            await st.execute(insert_stmt)
            
        await st.commit()
        
    await message.answer(f"Теги применены: {', '.join(new_tags)}")