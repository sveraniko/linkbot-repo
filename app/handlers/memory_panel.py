from __future__ import annotations
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from html import escape
import datetime as dt
import uuid
import re

from app.db import session_scope
from app.models import Artifact, Project, Tag, artifact_tags
from app.services.memory import get_active_project, _ensure_user_state, get_chat_flags
from app.handlers.keyboard import main_reply_kb
from app.ui import show_panel
from app.services.artifacts import create_import

router = Router(name="memory_panel")

# Date extraction patterns for auto-tags
DATE_PATTERNS = [
    re.compile(r"[_-](\d{4})(\d{2})(\d{2})[_-]"),         # _YYYYMMDD_ or -YYYYMMDD-
    re.compile(r"[-](\d{4})[-](\d{2})[-](\d{2})"),       # -YYYY-MM-DD
]

def extract_doc_date(filename: str) -> str | None:
    """Extract document date from filename if possible."""
    for pat in DATE_PATTERNS:
        m = pat.search(filename or "")
        if m:
            year, month, day = m.group(1), m.group(2), m.group(3)
            # Validate date
            try:
                dt.date(int(year), int(month), int(day))
                return f"{year}-{month}-{day}"
            except ValueError:
                continue
    return None

def auto_tags_for_single_file(filename: str) -> list[str]:
    """Generate auto-tags for single file import."""
    tags = [f"rel-{dt.date.today():%Y-%m-%d}"]
    d = extract_doc_date(filename)
    if d:
        tags.append(f"doc-{d}")
    tags.append(f"batch-{str(uuid.uuid4())[:6]}")
    return tags

# Memory panel main keyboard
def _memory_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 List", callback_data="mem:list:1")
    builder.button(text="🧾 Show", callback_data="mem:show")
    builder.button(text="🧹 Clear…", callback_data="mem:clear_confirm")
    builder.button(text="➕ Add note", callback_data="mem:add_note")
    builder.button(text="📥 Import last", callback_data="mem:import_last")
    builder.adjust(2, 2, 1)
    return builder.as_markup()

# Memory panel entry point
@router.message(F.text == "🧠 Memory")
async def memory_open(message: Message):
    """Open Memory panel."""
    if not message.from_user:
        return
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id)
        if not proj:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Нет активного проекта. Создай или выбери.", reply_markup=main_reply_kb(chat_on))
            return
            
        await message.answer("Memory панель:", reply_markup=_memory_kb())

# List artifacts with pagination
@router.callback_query(F.data.startswith("mem:list:"))
async def memory_list(cb: CallbackQuery):
    if not cb.from_user or not cb.data:
        return await cb.answer("Invalid user")
    
    try:
        page = int(cb.data.split(":")[2])
    except (IndexError, ValueError):
        page = 1
    
    async with session_scope() as st:
        proj = await get_active_project(st, cb.from_user.id)
        if not proj:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет активного проекта", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет активного проекта")
            
        page_size = 10
        offset = (page - 1) * page_size
        
        # Get artifacts with their tags using selectinload to avoid duplicates
        stmt = (
            select(Artifact)
            .options(selectinload(Artifact.tags))
            .where(Artifact.project_id == proj.id)
            .order_by(Artifact.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await st.execute(stmt)
        artifacts = result.scalars().all()
        
        if not artifacts:
            lines = ["<b>Memory — список</b>", "Нет записей."]
        else:
            lines = [f"<b>Memory — список (стр. {page})</b>"]
            for i, art in enumerate(artifacts, start=1 + offset):
                # Get tags for this artifact
                tag_names = [t.name for t in art.tags] if art.tags else []
                tags_str = f" [{', '.join(escape(tag) for tag in tag_names[:3])}]" if tag_names else ""
                title = escape((art.title or str(art.id))[:50])
                lines.append(f"{i}. 📄 {title}{tags_str}")
        
        # Build keyboard with pagination and item actions
        builder = InlineKeyboardBuilder()
        
        # Add item action buttons with correct callback data format
        for art in artifacts:
            art_id = art.id
            title = (art.title or str(art.id))[:20]
            builder.button(text=f"🗑 {title}", callback_data=f"mem:delete:{art_id}")
            builder.button(text=f"🏷 {title}", callback_data=f"mem:tag:{art_id}")
            builder.button(text=f"🧷 {title}", callback_data=f"mem:pin:{art_id}")
            builder.button(text=f"🔎 {title}", callback_data=f"mem:ask:{art_id}")
        builder.adjust(2)
        
        # Pagination
        if page > 1:
            builder.button(text="⬅️ Назад", callback_data=f"mem:list:{page-1}")
        builder.button(text="📋 Обновить", callback_data=f"mem:list:{page}")
        # Check if there are more items
        if len(artifacts) == page_size:
            builder.button(text="Вперёд ➡️", callback_data=f"mem:list:{page+1}")
        builder.adjust(2)
        
        # Bottom menu
        builder.button(text="🏠 Назад", callback_data="mem:main")
        builder.adjust(1)
        
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                           "\n".join(lines), builder.as_markup())
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

# Show memory summary
@router.callback_query(F.data == "mem:show")
async def memory_show(cb: CallbackQuery):
    if not cb.from_user:
        return await cb.answer("Invalid user")
    
    async with session_scope() as st:
        proj = await get_active_project(st, cb.from_user.id)
        if not proj:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет активного проекта", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет активного проекта")
            
        # Get counts by kind using explicit COUNT queries
        import_stmt = select(func.count(Artifact.id)).where(Artifact.project_id == proj.id, Artifact.kind == "import")
        import_result = await st.execute(import_stmt)
        import_count = import_result.scalar_one_or_none() or 0
        
        note_stmt = select(func.count(Artifact.id)).where(Artifact.project_id == proj.id, Artifact.kind == "note")
        note_result = await st.execute(note_stmt)
        note_count = note_result.scalar_one_or_none() or 0
        
        answer_stmt = select(func.count(Artifact.id)).where(Artifact.project_id == proj.id, Artifact.kind == "answer")
        answer_result = await st.execute(answer_stmt)
        answer_count = answer_result.scalar_one_or_none() or 0
        
        total_stmt = select(func.count(Artifact.id)).where(Artifact.project_id == proj.id)
        total_result = await st.execute(total_stmt)
        total_count = total_result.scalar_one_or_none() or 0
        
        # Get recent dates
        date_stmt = (
            select(Artifact.created_at)
            .where(Artifact.project_id == proj.id)
            .order_by(Artifact.created_at.desc())
            .limit(5)
        )
        date_result = await st.execute(date_stmt)
        recent_dates = [row[0].date() for row in date_result.all()]
        
        # Get recent rel- dates from tags using proper JOIN
        rel_date_stmt = (
            select(Tag.name)
            .select_from(Artifact)
            .join(artifact_tags, Artifact.id == artifact_tags.c.artifact_id)
            .join(Tag, artifact_tags.c.tag_name == Tag.name)
            .where(
                Artifact.project_id == proj.id,
                Tag.name.like("rel-%")
            )
            .order_by(Artifact.created_at.desc())
            .limit(10)
        )
        rel_date_result = await st.execute(rel_date_stmt)
        rel_dates = []
        for row in rel_date_result.all():
            # Extract date from tag name like "rel-2025-09-15"
            tag_name = row[0]
            if tag_name.startswith("rel-"):
                try:
                    date_part = tag_name[4:]  # Remove "rel-" prefix
                    # Validate it's a real date
                    dt.date.fromisoformat(date_part)
                    rel_dates.append(date_part)
                except (ValueError, IndexError):
                    pass
        
        lines = ["<b>Memory — сводка</b>"]
        lines.append(f"Всего: {total_count}")
        lines.append(f"  import: {import_count}")
        lines.append(f"  note: {note_count}")
        lines.append(f"  answer: {answer_count}")
        
        if recent_dates:
            lines.append(f"Последние даты: {', '.join(str(d) for d in sorted(set(recent_dates), reverse=True)[:3])}")
            
        if rel_dates:
            lines.append(f"Последние rel-даты: {', '.join(sorted(set(rel_dates), reverse=True)[:3])}")
        
        # Build keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Назад", callback_data="mem:main")
        
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                           "\n".join(lines), builder.as_markup())
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

# Clear confirmation
@router.callback_query(F.data == "mem:clear_confirm")
async def memory_clear_confirm(cb: CallbackQuery):
    if not cb.from_user:
        return await cb.answer("Invalid user")
    
    async with session_scope() as st:
        proj = await get_active_project(st, cb.from_user.id)
        if not proj:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет активного проекта", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет активного проекта")
            
        lines = [
            "<b>Memory — очистка</b>",
            f"Вы уверены, что хотите удалить ВСЕ записи в проекте <b>{escape(proj.name)}</b>?",
            "Это действие нельзя отменить."
        ]
        
        # Build keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Да, удалить всё", callback_data="mem:clear_execute")
        builder.button(text="❌ Отмена", callback_data="mem:main")
        builder.adjust(1)
        
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                           "\n".join(lines), builder.as_markup())
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

# Execute clear
@router.callback_query(F.data == "mem:clear_execute")
async def memory_clear_execute(cb: CallbackQuery):
    if not cb.from_user:
        return await cb.answer("Invalid user")
    
    async with session_scope() as st:
        proj = await get_active_project(st, cb.from_user.id)
        if not proj:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет активного проекта", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет активного проекта")
            
        # Delete all artifacts in the project
        stmt = delete(Artifact).where(Artifact.project_id == proj.id)
        result = await st.execute(stmt)
        deleted_count = result.rowcount
        await st.commit()
        
        lines = [
            "<b>Memory — очистка</b>",
            f"Удалено записей: {deleted_count}",
            f"Проект: <b>{escape(proj.name)}</b>"
        ]
        
        # Build keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Назад", callback_data="mem:main")
        
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                           "\n".join(lines), builder.as_markup())
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer("Очищено")

# Add note
@router.callback_query(F.data == "mem:add_note")
async def memory_add_note(cb: CallbackQuery):
    if not cb.from_user:
        return await cb.answer("Invalid user")
    
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Введите текст заметки:", reply_markup=ForceReply(selective=True))
        
    # Always include reply keyboard
    async with session_scope() as st:
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

# Handle note creation
@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Введите текст заметки:"))
async def memory_create_note(message: Message):
    if not message.from_user or not message.text:
        return
    
    text = message.text.strip()
    if not text:
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Пустая заметка.", reply_markup=main_reply_kb(chat_on))
        return
    
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id)
        if not proj:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Нет активного проекта.", reply_markup=main_reply_kb(chat_on))
            return
            
        # Create note artifact
        art = Artifact(
            project_id=proj.id,
            kind="note",
            title=text[:50] + ("..." if len(text) > 50 else ""),
            raw_text=text
        )
        st.add(art)
        await st.flush()
        
        # Add default tags
        default_tags = ["note", f"rel-{dt.date.today():%Y-%m-%d}"]
        for tag_name in default_tags:
            # Get or create tag
            tag_stmt = select(Tag).where(Tag.name == tag_name)
            tag_result = await st.execute(tag_stmt)
            tag = tag_result.scalar_one_or_none()
            if not tag:
                tag = Tag(name=tag_name)
                st.add(tag)
                await st.flush()
            
            # Link tag to artifact
            link_stmt = artifact_tags.insert().values(artifact_id=art.id, tag_name=tag.name)
            await st.execute(link_stmt)
        
        await st.commit()
        
        lines = [
            "<b>Memory — заметка добавлена</b>",
            f"Текст: {escape(text[:100])}{'...' if len(text) > 100 else ''}"
        ]
        
        # Build keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Назад", callback_data="mem:main")
        builder.button(text="🏷 Теги", callback_data=f"mem:tag:{art.id}")
        builder.adjust(1)
        
        await message.answer("\n".join(lines), reply_markup=builder.as_markup())
        
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, message.from_user.id)
        await message.answer("...", reply_markup=main_reply_kb(chat_on))

# Import last file
@router.callback_query(F.data == "mem:import_last")
async def memory_import_last(cb: CallbackQuery):
    if not cb.from_user:
        return await cb.answer("Invalid user")
    
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id)
        if not stt.last_doc_file_id or not stt.last_doc_name:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет последнего файла", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет последнего файла")
            
        # Try to import the last file using the same helper as Actions
        from app.handlers.import_file import import_last_for_user
        if cb.message and isinstance(cb.message, Message):
            success = await import_last_for_user(cb.message, st, None)
            
            if success:
                # Show success message and return to memory panel
                builder = InlineKeyboardBuilder()
                builder.button(text="🏠 Назад", callback_data="mem:main")
                
                if cb.message.bot:
                    await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                                   "Файл импортирован", builder.as_markup())
            else:
                # Always include reply keyboard
                chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
                if cb.message and isinstance(cb.message, Message):
                    await cb.message.answer("Ошибка импорта", reply_markup=main_reply_kb(chat_on))
                await cb.answer("Ошибка импорта")
                
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

# Return to main memory panel
@router.callback_query(F.data == "mem:main")
async def memory_main(cb: CallbackQuery):
    if not cb.from_user:
        return await cb.answer("Invalid user")
    
    async with session_scope() as st:
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                           "Memory панель:", _memory_kb())
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

# Delete artifact - fixed to use artifact ID directly
@router.callback_query(F.data.startswith("mem:delete:"))
async def memory_delete(cb: CallbackQuery):
    if not cb.from_user or not cb.data:
        return await cb.answer("Invalid user")
    
    try:
        art_id = int(cb.data.split(":")[2])
    except (IndexError, ValueError):
        # Always include reply keyboard
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Некорректный ID записи", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Некорректный ID записи")
    
    async with session_scope() as st:
        # Get artifact
        stmt = select(Artifact).where(Artifact.id == art_id)
        result = await st.execute(stmt)
        art = result.scalar_one_or_none()
        
        if not art:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Запись не найдена", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Запись не найдена")
            
        # Check project ownership
        proj = await get_active_project(st, cb.from_user.id)
        if not proj or art.project_id != proj.id:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет доступа", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет доступа")
            
        title = art.title or str(art.id)
        
        # Delete artifact by ID
        delete_stmt = delete(Artifact).where(Artifact.id == art_id)
        result = await st.execute(delete_stmt)
        deleted_count = result.rowcount
        await st.commit()
        
        lines = [
            "<b>Memory — запись удалена</b>",
            f"Удалено записей: {deleted_count}",
            f"Запись: {escape(title[:100])}{'...' if len(title) > 100 else ''}"
        ]
        
        # Build keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Назад", callback_data="mem:main")
        
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                           "\n".join(lines), builder.as_markup())
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer("Удалено")

# Tag artifact
@router.callback_query(F.data.startswith("mem:tag:"))
async def memory_tag(cb: CallbackQuery):
    if not cb.from_user or not cb.data:
        return await cb.answer("Invalid user")
    
    try:
        art_id = int(cb.data.split(":")[2])
    except (IndexError, ValueError):
        # Always include reply keyboard
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Некорректный ID записи", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Некорректный ID записи")
    
    async with session_scope() as st:
        # Get artifact with tags
        stmt = select(Artifact).options(selectinload(Artifact.tags)).where(Artifact.id == art_id)
        result = await st.execute(stmt)
        art = result.scalar_one_or_none()
        
        if not art:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Запись не найдена", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Запись не найдена")
            
        # Check project ownership
        proj = await get_active_project(st, cb.from_user.id)
        if not proj or art.project_id != proj.id:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет доступа", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет доступа")
            
        # Get current tags
        tag_names = [t.name for t in art.tags] if art.tags else []
        
        lines = [
            "<b>Memory — теги</b>",
            f"Запись: {escape((art.title or str(art.id))[:50])}",
            f"Текущие теги: {', '.join(escape(tag) for tag in tag_names) if tag_names else '—'}",
            "Введите новые теги через запятую:"
        ]
        
        # Store artifact ID in user state for the reply handler
        stt = await _ensure_user_state(st, cb.from_user.id)
        stt.last_batch_ids = str(art_id)  # Reuse this field to store artifact ID
        await st.commit()
        
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("\n".join(lines), reply_markup=ForceReply(selective=True))
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

# Handle tag update
@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Введите новые теги через запятую:"))
async def memory_update_tags(message: Message):
    if not message.from_user or not message.text:
        return
    
    tags_text = message.text.strip()
    tag_names = [tag.strip() for tag in tags_text.split(",") if tag.strip()] if tags_text else []
    
    async with session_scope() as st:
        stt = await _ensure_user_state(st, message.from_user.id)
        if not stt.last_batch_ids:  # Contains artifact ID
            chat_on, *_ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Ошибка: не найдена запись", reply_markup=main_reply_kb(chat_on))
            return
            
        try:
            art_id = int(stt.last_batch_ids)
        except ValueError:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Ошибка: некорректный ID записи", reply_markup=main_reply_kb(chat_on))
            return
            
        # Get artifact
        stmt = select(Artifact).where(Artifact.id == art_id)
        result = await st.execute(stmt)
        art = result.scalar_one_or_none()
        
        if not art:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Запись не найдена", reply_markup=main_reply_kb(chat_on))
            return
            
        # Check project ownership
        proj = await get_active_project(st, message.from_user.id)
        if not proj or art.project_id != proj.id:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Нет доступа", reply_markup=main_reply_kb(chat_on))
            return
            
        # Clear existing tags
        delete_stmt = artifact_tags.delete().where(artifact_tags.c.artifact_id == art_id)
        await st.execute(delete_stmt)
        
        # Add new tags
        for tag_name in tag_names:
            # Get or create tag
            tag_stmt = select(Tag).where(Tag.name == tag_name)
            tag_result = await st.execute(tag_stmt)
            tag = tag_result.scalar_one_or_none()
            if not tag:
                tag = Tag(name=tag_name)
                st.add(tag)
                await st.flush()
            
            # Link tag to artifact
            link_stmt = artifact_tags.insert().values(artifact_id=art.id, tag_name=tag.name)
            await st.execute(link_stmt)
        
        await st.commit()
        
        lines = [
            "<b>Memory — теги обновлены</b>",
            f"Запись: {escape((art.title or str(art.id))[:50])}",
            f"Новые теги: {', '.join(escape(tag) for tag in tag_names) if tag_names else '—'}"
        ]
        
        # Build keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Назад", callback_data="mem:main")
        
        await message.answer("\n".join(lines), reply_markup=builder.as_markup())
        
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, message.from_user.id)
        await message.answer("...", reply_markup=main_reply_kb(chat_on))

# Pin artifact
@router.callback_query(F.data.startswith("mem:pin:"))
async def memory_pin(cb: CallbackQuery):
    if not cb.from_user or not cb.data:
        return await cb.answer("Invalid user")
    
    try:
        art_id = int(cb.data.split(":")[2])
    except (IndexError, ValueError):
        # Always include reply keyboard
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Некорректный ID записи", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Некорректный ID записи")
    
    async with session_scope() as st:
        # Get artifact
        stmt = select(Artifact).where(Artifact.id == art_id)
        result = await st.execute(stmt)
        art = result.scalar_one_or_none()
        
        if not art:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Запись не найдена", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Запись не найдена")
            
        # Check project ownership
        proj = await get_active_project(st, cb.from_user.id)
        if not proj or art.project_id != proj.id:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет доступа", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет доступа")
            
        # Toggle pin status
        art.pinned = not art.pinned
        await st.commit()
        
        status = "закреплена" if art.pinned else "откреплена"
        lines = [
            "<b>Memory — закрепление</b>",
            f"Запись {escape((art.title or str(art.id))[:50])} {status}"
        ]
        
        # Build keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Назад", callback_data="mem:main")
        
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                           "\n".join(lines), builder.as_markup())
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer(f"Запись {status}")

# Ask about artifact
@router.callback_query(F.data.startswith("mem:ask:"))
async def memory_ask(cb: CallbackQuery):
    if not cb.from_user or not cb.data:
        return await cb.answer("Invalid user")
    
    try:
        art_id = int(cb.data.split(":")[2])
    except (IndexError, ValueError):
        # Always include reply keyboard
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Некорректный ID записи", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Некорректный ID записи")
    
    async with session_scope() as st:
        # Get artifact
        stmt = select(Artifact).where(Artifact.id == art_id)
        result = await st.execute(stmt)
        art = result.scalar_one_or_none()
        
        if not art:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Запись не найдена", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Запись не найдена")
            
        # Check project ownership
        proj = await get_active_project(st, cb.from_user.id)
        if not proj or art.project_id != proj.id:
            # Always include reply keyboard
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Нет доступа", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Нет доступа")
            
        # Set this artifact as selected in ASK
        stt = await _ensure_user_state(st, cb.from_user.id)
        stt.selected_artifact_ids = str(art_id)
        stt.ask_armed = True
        await st.commit()
        
        # Redirect to ASK panel
        from app.handlers.ask import _panel_kb, _calc_budget_label
        budget_label = await _calc_budget_label(st, [art_id])
        
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                           "ASK панель:", _panel_kb(1, budget_label, stt.auto_clear_selection))
            
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer("Выбрано для ASK")