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
from html import escape
# Import the new tags service
from app.services.tags import get_presets

router = Router()

# общие константы
GENERAL_TAG_PROMPT = "Свои теги (через запятую):"
IMPORT_TAG_PROMPT_PREFIX = "Свои теги для импорта #"

# текущее выбранное множество держим в памяти на 2 минуты (простая мапа)
TAG_CACHE: dict[int, set[str]] = {}


# --- Теги для импорта (по artifact_id) ---
IMP_TAG_CACHE: dict[int, set[str]] = {}


def _project_pick_kb(msg_id: int, purpose: str, projects):
    # purpose: "save" | "sum"
    rows = []
    for p in projects:
        rows.append([InlineKeyboardButton(text=f"{escape(p.name)}", callback_data=f"ans:pickproj:{purpose}:{msg_id}:{p.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_tag_kb(tags: list[str], msg_id: int):
    # 3 в ряд, плюс Done / Free input
    rows, row = [], []
    for i, t in enumerate(tags, 1):
        row.append(InlineKeyboardButton(text=f"{escape(t)}", callback_data=f"ans:tagtoggle:{msg_id}:{t}"))
        if i % 3 == 0:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"ans:tagdone:{msg_id}"),
                 InlineKeyboardButton(text="✍️ Свои", callback_data=f"ans:tagfree:{msg_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("ans:save:"))
async def ans_save(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
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
                        # Get chat_on flag to rebuild keyboard with correct state
                        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                        return await cb.message.answer("Сначала создай проект: Actions → Projects → ➕ New", reply_markup=build_reply_kb(chat_on))
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
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
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
                        # Get chat_on flag to rebuild keyboard with correct state
                        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                        return await cb.message.answer("Сначала создай проект: Actions → Projects → ➕ New", reply_markup=build_reply_kb(chat_on))
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
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer("📌 Суммаризация сохранена и закреплена.", reply_markup=build_reply_kb(chat_on))
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
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    # удаляем СВОЁ сообщение
    try:
        if cb.message and isinstance(cb.message, Message):
            await cb.message.delete()
    except:
        pass
    # аккуратная плашка (самоудалится)
    note = None
    if cb.message and isinstance(cb.message, Message):
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            note = await cb.message.answer("🧹 Очищено", reply_markup=build_reply_kb(chat_on))
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
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    msg_id = int(cb.data.split(":")[-1])
    async with session_scope() as st:
        # project-specific пресеты
        bm = (await st.execute(sa.select(BotMessage).where(BotMessage.tg_message_id==msg_id))).scalars().first()
        pid = bm.project_id if bm else None
        presets = await get_presets(st, cb.from_user.id if cb.from_user else 0, pid)
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
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
    await cb.answer(f"{'+' if tag in cur else '-'} {escape(tag)}")


@router.callback_query(F.data.startswith("ans:tagdone:"))
async def ans_tag_done(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
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
                    # Get chat_on flag to rebuild keyboard with correct state
                    chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                    return await cb.message.answer("Сначала выбери проект в Actions → Projects.", reply_markup=build_reply_kb(chat_on))
                proj = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
                if proj:
                    target_pid = proj.id
                else:
                    if cb.message and isinstance(cb.message, Message):
                        # Get chat_on flag to rebuild keyboard with correct state
                        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                        return await cb.message.answer("Сначала выбери проект в Actions → Projects.", reply_markup=build_reply_kb(chat_on))
            base = Artifact(project_id=target_pid, kind="answer", title="Chat answer", raw_text=text, pinned=False)
            st.add(base)
            await st.flush()
            bm.artifact_id = base.id
            bm.saved = True
            st.add(bm)
        await st.execute(sa.delete(artifact_tags).where(artifact_tags.c.artifact_id == bm.artifact_id))
        # Create tag objects first to ensure they exist in the tags table
        tag_objects = []
        for tag_name in tags:
            # Check if tag exists
            tag_stmt = sa.select(Tag).where(Tag.name == tag_name)
            tag_result = await st.execute(tag_stmt)
            tag = tag_result.scalar_one_or_none()
            
            if not tag:
                # Create new tag if it doesn't exist
                tag = Tag(name=tag_name)
                st.add(tag)
                await st.flush()
                
            tag_objects.append(tag)
        
        # Insert into artifact_tags with proper tag_name references
        insert_data = [{"artifact_id": bm.artifact_id, "tag_name": tag.name} for tag in tag_objects]
        if insert_data:
            await st.execute(sa.insert(artifact_tags).values(insert_data))
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"🏷 Теги: {escape(', '.join(tags))}", reply_markup=build_reply_kb(chat_on))
    TAG_CACHE.pop(msg_id, None)
    await cb.answer()


@router.callback_query(F.data.startswith("ans:tagfree:"))
async def ans_tag_free(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    msg_id = int(cb.data.split(":")[-1])
    if cb.message and isinstance(cb.message, Message):
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(GENERAL_TAG_PROMPT, reply_markup=ForceReply(selective=True))
    await cb.answer()


# общий фри-ввод тегов для ОТВЕТА (не импорта!)
@router.message(F.reply_to_message & (F.reply_to_message.text == GENERAL_TAG_PROMPT))
async def tags_free_reply(message: Message):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    tags = [t.strip() for t in (message.text or "").split(",") if t.strip()]
    if not tags: 
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        return await message.answer("Пусто.", reply_markup=build_reply_kb(chat_on))
    # Применим к последнему BotMessage пользователя, аналогично предыдущему коду
    async with session_scope() as st:
        bm = (await st.execute(sa.select(BotMessage).where(BotMessage.user_id==(message.from_user.id if message.from_user else 0))
              .order_by(BotMessage.created_at.desc()).limit(1))).scalars().first()
        if not bm: 
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            return await message.answer("Не нашёл сообщение для тегов.", reply_markup=build_reply_kb(chat_on))
        text = message.reply_to_message and (message.reply_to_message.text or "") or ""
        if message.reply_to_message and message.reply_to_message.caption:
            text = message.reply_to_message.caption
        target_pid = bm.project_id
        if not bm.saved or not bm.artifact_id:
            if not target_pid: 
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
                return await message.answer("Сначала выбери проект.", reply_markup=build_reply_kb(chat_on))
            proj = await get_active_project(st, message.from_user.id if message.from_user else 0)
            if proj:
                target_pid = proj.id
            else:
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
                return await message.answer("Сначала выбери проект.", reply_markup=build_reply_kb(chat_on))
            base = Artifact(project_id=target_pid, kind="answer", title="Chat answer", raw_text=text, pinned=False)
            st.add(base)
            await st.flush()
            bm.artifact_id = base.id
            bm.saved = True
            st.add(bm)
        await st.execute(sa.delete(artifact_tags).where(artifact_tags.c.artifact_id == bm.artifact_id))
        # Create tag objects first to ensure they exist in the tags table
        tag_objects = []
        for tag_name in tags:
            # Check if tag exists
            tag_stmt = sa.select(Tag).where(Tag.name == tag_name)
            tag_result = await st.execute(tag_stmt)
            tag = tag_result.scalar_one_or_none()
            
            if not tag:
                # Create new tag if it doesn't exist
                tag = Tag(name=tag_name)
                st.add(tag)
                await st.flush()
                
            tag_objects.append(tag)
        
        # Insert into artifact_tags with proper tag_name references
        insert_data = [{"artifact_id": bm.artifact_id, "tag_name": tag.name} for tag in tag_objects]
        if insert_data:
            await st.execute(sa.insert(artifact_tags).values(insert_data))
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        response_text = f"🏷 Теги: {escape(', '.join(tags))}"
    await message.answer(response_text, reply_markup=build_reply_kb(chat_on))


@router.callback_query(F.data.startswith("imp:tag:"))
async def imp_tag(cb: CallbackQuery):
    from app.services.tags import get_presets
    if not cb.data:
        return await cb.answer("Invalid data")
    art_id = int(cb.data.split(":")[-1])
    async with session_scope() as st:
        from app.services.memory import get_active_project
        bm = None
        if cb.from_user:
            bm = (await st.execute(sa.select(BotMessage)
                  .where(BotMessage.user_id == cb.from_user.id)
                  .order_by(BotMessage.created_at.desc()).limit(1))).scalars().first()
        pid = None
        if bm:
            pid = bm.project_id
        else:
            active_proj = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
            if active_proj:
                pid = active_proj.id
        presets = await get_presets(st, cb.from_user.id if cb.from_user else 0, pid)
    IMP_TAG_CACHE[art_id] = set()
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Выбери теги (тап по кнопкам), потом «Готово».",
                                reply_markup=build_imp_tag_kb(presets, art_id))  # reuse разметки — msg_id нам не важен
    await cb.answer()

# Добавим отдельные колбэки для импорта:
@router.callback_query(F.data.startswith("imp:tagtoggle:"))
async def imp_tag_toggle(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    parts = cb.data.split(":")
    if len(parts) < 4:
        return await cb.answer("Invalid data format")
    _, _, art_id, tag = parts
    art_id = int(art_id)
    cur = IMP_TAG_CACHE.get(art_id, set())
    if tag in cur: cur.remove(tag)
    else: cur.add(tag)
    IMP_TAG_CACHE[art_id] = cur
    await cb.answer(f"{'+' if tag in cur else '-'} {tag}")

@router.callback_query(F.data.startswith("imp:tagdone:"))
async def imp_tag_done(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    art_id = int(cb.data.split(":")[-1])
    tags = sorted(IMP_TAG_CACHE.get(art_id, set()))
    if not tags:
        return await cb.answer("Не выбрано ни одного тега.", show_alert=True)
    async with session_scope() as st:
        # перезапишем теги
        await st.execute(sa.delete(artifact_tags).where(artifact_tags.c.artifact_id == art_id))
        # Create tag objects first to ensure they exist in the tags table
        tag_objects = []
        for tag_name in tags:
            # Check if tag exists
            tag_stmt = sa.select(Tag).where(Tag.name == tag_name)
            tag_result = await st.execute(tag_stmt)
            tag = tag_result.scalar_one_or_none()
            
            if not tag:
                # Create new tag if it doesn't exist
                tag = Tag(name=tag_name)
                st.add(tag)
                await st.flush()
                
            tag_objects.append(tag)
        
        # Insert into artifact_tags with proper tag_name references
        insert_data = [{"artifact_id": art_id, "tag_name": tag.name} for tag in tag_objects]
        if insert_data:
            await st.execute(sa.insert(artifact_tags).values(insert_data))
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"🏷 Теги для импорта: {escape(', '.join(tags))}", reply_markup=build_reply_kb(chat_on))
    IMP_TAG_CACHE.pop(art_id, None)
    await cb.answer()

@router.callback_query(F.data.startswith("imp:tagfree:"))
async def imp_tag_free(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    art_id = int(cb.data.split(":")[-1])
    if cb.message and isinstance(cb.message, Message):
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(f"{IMPORT_TAG_PROMPT_PREFIX}{art_id} (через запятую):", reply_markup=ForceReply(selective=True))
    await cb.answer()

# фри-ввод тегов для ИМПОРТА (по artifact_id)
@router.message(
    F.reply_to_message & F.reply_to_message.text.regexp(r"^Свои теги для импорта #\d+")
)
async def imp_tags_free_reply(message: Message):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    import re
    if not message.reply_to_message or not message.reply_to_message.text:
        return
    m = re.search(r"^Свои теги для импорта #(\d+)", message.reply_to_message.text)
    if not m: return
    art_id = int(m.group(1))
    tags = [t.strip() for t in (message.text or "").split(",") if t.strip()]
    if not tags: 
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        return await message.answer("Пусто.", reply_markup=build_reply_kb(chat_on))
    async with session_scope() as st:
        await st.execute(sa.delete(artifact_tags).where(artifact_tags.c.artifact_id == art_id))
        # Create tag objects first to ensure they exist in the tags table
        tag_objects = []
        for tag_name in tags:
            # Check if tag exists
            tag_stmt = sa.select(Tag).where(Tag.name == tag_name)
            tag_result = await st.execute(tag_stmt)
            tag = tag_result.scalar_one_or_none()
            
            if not tag:
                # Create new tag if it doesn't exist
                tag = Tag(name=tag_name)
                st.add(tag)
                await st.flush()
                
            tag_objects.append(tag)
        
        # Insert into artifact_tags with proper tag_name references
        insert_data = [{"artifact_id": art_id, "tag_name": tag.name} for tag in tag_objects]
        if insert_data:
            await st.execute(sa.insert(artifact_tags).values(insert_data))
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
    await message.answer(f"🏷 Теги для импорта: {escape(', '.join(tags))}", reply_markup=build_reply_kb(chat_on))


@router.callback_query(F.data.startswith("imp:del:"))
async def imp_del(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    art_id = int(cb.data.split(":")[-1])
    async with session_scope() as st:
        await st.execute(sa.delete(Artifact).where(Artifact.id == art_id))
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"🗑 Импорт #{art_id} удалён", reply_markup=build_reply_kb(chat_on))
    await cb.answer()


# где-то рядом с build_tag_kb — версия для импорта
def build_imp_tag_kb(tags: list[str], art_id: int):
    rows, row = [], []
    for i, t in enumerate(tags, 1):
        row.append(InlineKeyboardButton(text=f"{escape(t)}", callback_data=f"imp:tagtoggle:{art_id}:{t}"))
        if i % 3 == 0:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([
        InlineKeyboardButton(text="✅ Готово", callback_data=f"imp:tagdone:{art_id}"),
        InlineKeyboardButton(text="✍️ Свои", callback_data=f"imp:tagfree:{art_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
