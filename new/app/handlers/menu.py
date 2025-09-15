# app/handlers/menu.py
from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, session_scope
from app.config import settings
from app.services.memory import (
    get_active_project, set_context_filters,
    get_preferred_model, set_preferred_model, gather_context,
    list_projects, get_linked_project_ids, get_active_project,
    link_toggle_project, set_active_project, get_chat_flags, _ensure_user_state
)
from app.llm import ask_llm
from app.models import Project
from typing import List
from app.ui import show_panel
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape

# Add Berlin timezone
BERLIN = ZoneInfo("Europe/Berlin")

router = Router()

def kb_menu(model: str) -> InlineKeyboardMarkup:
    """New Actions panel layout as specified"""
    rows = [
        # Row 1: Status and Memory
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="status:show"),
            InlineKeyboardButton(text="🧠 Memory", callback_data="mem:main"),
        ],
        # Row 2: Import and Scope
        [
            InlineKeyboardButton(text="📥 Импорт (последний файл)", callback_data="wizard:import"),
            InlineKeyboardButton(text="🎯 Scope", callback_data="scope:toggle"),
        ],
        # Row 3: Export and Repo
        [
            InlineKeyboardButton(text="📤 Export", callback_data="export:open"),
            InlineKeyboardButton(text="🔗 Repo", callback_data="repo:open"),
        ],
        # Row 4: Cleanup and Reset filters
        [
            InlineKeyboardButton(text="🧹 Cleanup by date", callback_data="cleanup:open"),
            InlineKeyboardButton(text="🧽 Сброс фильтров", callback_data="ctx:reset"),
        ],
        # Row 5+: Additional options
        [
            InlineKeyboardButton(text="🧩 API", callback_data="ctx:tags:api"),
            InlineKeyboardButton(text="DB", callback_data="ctx:tags:db"),
            InlineKeyboardButton(text="matching", callback_data="ctx:tags:matching"),
        ],
        [
            InlineKeyboardButton(text="📝 TODO", callback_data="ask:todo"),
            InlineKeyboardButton(text="⚠️ Risks", callback_data="ask:risks"),
            InlineKeyboardButton(text="📦 Release notes", callback_data="ask:relnotes"),
        ],
        # Model selection
        [
            InlineKeyboardButton(
                text=("⚙️ gpt-5 ✓" if model == "gpt-5" else "gpt-5"),
                callback_data="model:gpt-5",
            ),
            InlineKeyboardButton(
                text=("🧩 gpt-5-thinking ✓" if model == "gpt-5-thinking" else "gpt-5-thinking"),
                callback_data="model:gpt-5-thinking",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(Command("menu"))
async def menu(message: Message):
    async with session_scope() as session:
        model = await get_preferred_model(session, message.from_user.id if message.from_user else 0)
        # Delete the original "/menu" message to prevent chat clutter
        try:
            await message.delete()
        except:
            pass
        if message.bot and message.chat and message.from_user:
            await show_panel(session, message.bot, message.chat.id, message.from_user.id,
                             "Меню быстрых действий:", kb_menu(model))

@router.message(Command("actions"))
async def actions(message: Message):
    async with session_scope() as session:
        model = await get_preferred_model(session, message.from_user.id if message.from_user else 0)
        # Delete the original "/actions" message to prevent chat clutter
        try:
            await message.delete()
        except:
            pass
        if message.bot and message.chat and message.from_user:
            await show_panel(session, message.bot, message.chat.id, message.from_user.id,
                             "Панель действий:", kb_menu(model))

# ── Hints ───────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "hint:importzip")
async def hint_zip(cb: CallbackQuery):
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    from app.db import session_scope
    txt = (
        "Импорт ZIP:\n"
        "1) Прикрепите .zip как файл\n"
        "2) Ответом на сообщение отправьте:\n"
        "<code>/importzip tags code,snapshot,rev-YYYY-MM-DD</code>"
    )
    # Delete the panel and send hint
    if cb.message and isinstance(cb.message, Message):
        try:
            await cb.message.delete()
        except:
            pass
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(txt, reply_markup=build_reply_kb(chat_on))
    await cb.answer()

# ── Status (кнопка) ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "status:show")
async def status_show(cb: CallbackQuery):
    from app.db import session_scope
    from app.handlers.status import render_status
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    async with session_scope() as st:
        text = await render_status(st, cb.from_user.id if cb.from_user else 0)
        # Delete the panel and send status
        if cb.message and isinstance(cb.message, Message):
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(text, reply_markup=build_reply_kb(chat_on))
    await cb.answer()

# ── Context presets ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ctx:reset")
async def ctx_reset(cb: CallbackQuery):
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    async with session_scope() as st:
        await set_context_filters(st, cb.from_user.id if cb.from_user else 0, kinds_csv="", tags_csv="")
        await st.commit()
        # Delete the panel and send confirmation
        if cb.message and isinstance(cb.message, Message):
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer("Фильтры контекста сброшены. Используется вся память проекта.", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

@router.callback_query(F.data.startswith("ctx:tags:"))
async def ctx_presets(cb: CallbackQuery):
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    tag = cb.data.split(":")[-1]
    async with session_scope() as st:
        await set_context_filters(st, cb.from_user.id if cb.from_user else 0, tags_csv=tag)
        await st.commit()
        # Delete the panel and send confirmation
        if cb.message and isinstance(cb.message, Message):
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(f"Фильтры обновлены: tags={tag}", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

# ── Model switch ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("model:"))
async def model_switch(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    _, model = cb.data.split(":", 1)
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    async with session_scope() as session:
        applied = await set_preferred_model(session, cb.from_user.id if cb.from_user else 0, model)
        await session.commit()
        # Delete the panel and send confirmation
        if cb.message and isinstance(cb.message, Message):
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(session, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(f"Модель установлена: {applied}", reply_markup=build_reply_kb(chat_on))
    await cb.answer(f"Модель установлена: {applied}")

# ── Import wizard (последний файл) ─────────────────────────────────────────────

def _extract_doc_tag(name: str) -> str | None:
    m = re.search(r'(\d{4})(\d{2})(\d{2})', (name or "").lower())
    return f"doc-{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

@router.callback_query(F.data == "wizard:import")
async def wizard_import(cb: CallbackQuery):
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags, get_active_project, _ensure_user_state
    from app.services.artifacts import create_import
    from app.config import settings
    import zipfile
    import tempfile
    from pathlib import Path
    from app.ignore import load_pmignore, iter_text_files
    
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id if cb.from_user else 0)
        if not stt.last_doc_file_id:
            # Delete the panel first
            if cb.message and isinstance(cb.message, Message):
                try:
                    await cb.message.delete()
                except:
                    pass
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                await cb.message.answer("Нет «последнего файла». Пришлите .txt/.md/.json/.zip и повторите.", reply_markup=build_reply_kb(chat_on))
                return await cb.answer()

        # Check if we have a message and bot
        if not cb.message or not isinstance(cb.message, Message) or not cb.message.bot:
            return await cb.answer("Ошибка: нет доступа к боту")

        # Check if we have a valid file_id
        if not stt.last_doc_file_id:
            # Delete the panel first
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer("Нет «последнего файла». Пришлите .txt/.md/.json/.zip и повторите.", reply_markup=build_reply_kb(chat_on))
            return await cb.answer()

        # получаем bytes файла
        try:
            file = await cb.message.bot.get_file(stt.last_doc_file_id)
            if not file.file_path:
                # Delete the panel first
                try:
                    await cb.message.delete()
                except:
                    pass
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                await cb.message.answer("Не удалось получить путь к файлу", reply_markup=build_reply_kb(chat_on))
                return await cb.answer()
                
            fb = await cb.message.bot.download_file(file.file_path)
            if not fb:
                # Delete the panel first
                try:
                    await cb.message.delete()
                except:
                    pass
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                await cb.message.answer("Не удалось скачать файл", reply_markup=build_reply_kb(chat_on))
                return await cb.answer()
                
            data = fb.read()
            name = (stt.last_doc_name or "import.txt").lower()
        except Exception as e:
            # Delete the panel first
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(f"Ошибка при получении файла: {str(e)}", reply_markup=build_reply_kb(chat_on))
            return await cb.answer()

        proj = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
        if not proj:
            # Delete the panel first
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer("Сначала выбери проект: Actions → Projects.", reply_markup=build_reply_kb(chat_on))
            return await cb.answer()

        # autodate тег
        date_tag = f"rel-{datetime.now(BERLIN).date().isoformat()}"
        doc_tag = _extract_doc_tag(name)
        extra_tags = [date_tag] + ([doc_tag] if doc_tag else [])

        if name.endswith(".zip"):
            # Handle ZIP file import using the new import_zip_bytes function
            from app.services.import_zip import import_zip_bytes
            
            try:
                created_ids, batch_tag = await import_zip_bytes(st, proj, data, base_name=name, extra_tags=extra_tags,
                                                   chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
                await st.commit()
                
                # Store batch information
                stt = await _ensure_user_state(st, cb.from_user.id if cb.from_user else 0)
                stt.last_batch_ids = ",".join(map(str, created_ids))
                stt.last_batch_tag = batch_tag
                await st.commit()
                
                # Show buttons for batch operations
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                  InlineKeyboardButton(text="🏷 Tags for this import", callback_data="batch:tag"),
                  InlineKeyboardButton(text="🗑 Delete this import", callback_data="batch:delete"),
                ]])
                
                # Delete the panel and send confirmation with batch operations
                try:
                    await cb.message.delete()
                except:
                    pass
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                await cb.message.answer(
                  f"Импортировано из ZIP в проект: <b>{escape(proj.name)}</b>\n"
                  f"Файлов: {len(created_ids)}\n"
                  f"Партия: <code>{batch_tag}</code>",
                  reply_markup=kb
                )
                return await cb.answer()
                
            except Exception as e:
                # Delete the panel first
                try:
                    await cb.message.delete()
                except:
                    pass
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
                await cb.message.answer(f"Ошибка при импорте ZIP: {str(e)}", reply_markup=build_reply_kb(chat_on))
                return await cb.answer()

        text = data.decode("utf-8", errors="ignore")
        art = await create_import(st, proj, title=stt.last_doc_name or "import.txt", text=text,
                                chunk_size=settings.chunk_size, overlap=settings.chunk_overlap,
                                tags=extra_tags)
        await st.commit()
            
        # 🔹 Сразу предложим повесить теги на ЭТОТ импорт
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏷 Теги для импорта", callback_data=f"imp:tag:{art.id}"),
            InlineKeyboardButton(text="🗑 Delete this import", callback_data=f"imp:del:{art.id}"),
        ]])
        # Delete the panel and send confirmation with tagging option
        try:
            await cb.message.delete()
        except:
            pass
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        await cb.message.answer(f"Импортировано в проект: <b>{escape(proj.name)}</b>\nФайл: {escape(stt.last_doc_name or 'file')}\n"
                                f"Автотеги: {', '.join([t for t in extra_tags if t])}",
                                reply_markup=kb)
    await cb.answer()

# ── Quick ASK templates ────────────────────────────────────────────────────────

async def _ask_with_template(cb: CallbackQuery, template: str):
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    async with session_scope() as session:
        proj = await get_active_project(session, cb.from_user.id if cb.from_user else 0)
        if not proj:
            if cb.message and isinstance(cb.message, Message):
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(session, cb.from_user.id if cb.from_user else 0)
                await cb.message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>", reply_markup=build_reply_kb(chat_on))
            return
        ctx = await gather_context(session, proj, user_id=cb.from_user.id if cb.from_user else 0, max_chunks=settings.project_max_chunks)
        model = await get_preferred_model(session, cb.from_user.id if cb.from_user else 0)
        answer = await ask_llm(template, ctx, model=model)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer(answer)

@router.callback_query(F.data.startswith("ask:"))
async def ask_templates(cb: CallbackQuery):
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    kind = cb.data.split(":")[1]
    prompts = {
        "todo": "Сформируй подробный TODO-лист по проекту на ближайшую неделю на основе памяти.",
        "risks": "Определи ключевые риски проекта и предложи меры снижения. Ссылайся на контекст.",
        "relnotes": "Сгенерируй краткие release notes по последним изменениям и решениям, пригодные для команды.",
    }
    # Delete the panel first
    if cb.message and isinstance(cb.message, Message):
        try:
            await cb.message.delete()
        except:
            pass
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            # Send a message to rebuild the keyboard
            await cb.message.answer("✅ Шаблон выбран (LLM отключён)", reply_markup=build_reply_kb(chat_on))
    # Only call cb.answer() once at the end
    await cb.answer()

# --- Quiet / Scope / Sources ---
@router.callback_query(F.data == "quiet:toggle")
async def quiet_toggle(cb: CallbackQuery):
    from app.db import session_scope
    from app.services.memory import get_chat_flags, set_quiet_mode
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    async with session_scope() as st:
        _, quiet_on, _, _ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        newv = await set_quiet_mode(st, cb.from_user.id if cb.from_user else 0, on=not quiet_on)
        await st.commit()
        # Delete the panel and send confirmation
        if cb.message and isinstance(cb.message, Message):
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(f"Quiet mode: {'ON' if newv else 'OFF'}", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

@router.callback_query(F.data == "chat:toggle")
async def chat_toggle_cb(cb: CallbackQuery):
    from app.db import session_scope
    from app.services.memory import get_chat_flags, set_chat_mode
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    async with session_scope() as st:
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        new_on = not chat_on
        await set_chat_mode(st, cb.from_user.id if cb.from_user else 0, on=new_on)
        await st.commit()
    # Можно просто кратко подтвердить, клава внизу уже перестраивается через текстовый хендлер
    await cb.answer(f"Chat: {'ON' if new_on else 'OFF'}")

# Новые обработчики для Sources
def build_sources_kb(current: str) -> InlineKeyboardMarkup:
    opts = ["active", "linked", "all", "global"]
    row = [InlineKeyboardButton(text=("• "+o if o==current else o), callback_data=f"sources:set:{o}") for o in opts]
    return InlineKeyboardMarkup(inline_keyboard=[row])

@router.callback_query(F.data == "sources:toggle")
async def sources_toggle(cb: CallbackQuery):
    from app.db import session_scope
    async with session_scope() as st:
        _, _, current, _ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        kb = build_sources_kb(current)
        if cb.message and isinstance(cb.message, Message) and cb.message.bot and cb.message.chat and cb.from_user:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                             "Выбери источники (Sources):", kb)
    await cb.answer()

@router.callback_query(F.data.startswith("sources:set:"))
async def sources_set(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    _, _, val = cb.data.split(":")
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id if cb.from_user else 0)
        stt.sources_mode = val
        await st.commit()
        # Delete the panel and send confirmation
        if cb.message and isinstance(cb.message, Message):
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(f"Sources: {val}", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

# Новые обработчики для Scope
def build_scope_kb(current: str) -> InlineKeyboardMarkup:
    opts = ["auto", "project", "global"]
    row = [InlineKeyboardButton(text=("• "+o if o==current else o), callback_data=f"scope:set:{o}") for o in opts]
    return InlineKeyboardMarkup(inline_keyboard=[row])

@router.callback_query(F.data == "scope:toggle")
async def scope_toggle(cb: CallbackQuery):
    from app.db import session_scope
    async with session_scope() as st:
        _, _, _, current = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        kb = build_scope_kb(current)
        if cb.message and isinstance(cb.message, Message) and cb.message.bot and cb.message.chat and cb.from_user:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                             "Выбери область ответа (Scope):", kb)
    await cb.answer()

@router.callback_query(F.data.startswith("scope:set:"))
async def scope_set(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    _, _, val = cb.data.split(":")
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id if cb.from_user else 0)
        stt.scope_mode = val
        await st.commit()
        # Delete the panel and send confirmation
        if cb.message and isinstance(cb.message, Message):
            try:
                await cb.message.delete()
            except:
                pass
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer(f"Scope: {val}", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

# --- Projects list / link/unlink / activate ---
def build_projects_page(projects, linked_ids: set[int], active_id: int | None):
    rows = []
    rows.append([InlineKeyboardButton(text="➕ New project", callback_data="projects:new")])
    for p in projects:
        mark = "⭐ " if p.id in linked_ids else ""
        act = "✓ " if active_id == p.id else ""
        rows.append([
            InlineKeyboardButton(text=f"{act}{mark}{escape(p.name)}", callback_data="noop"),
            InlineKeyboardButton(text=("Unlink" if p.id in linked_ids else "Link"), callback_data=f"projects:link:{p.id}"),
            InlineKeyboardButton(text="Activate", callback_data=f"projects:activate:{p.id}")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data.startswith("projects:list"))
async def projects_list(cb: CallbackQuery):
    from app.db import session_scope
    async with session_scope() as st:
        allp = await list_projects(st)
        linked = set(await get_linked_project_ids(st, cb.from_user.id if cb.from_user else 0))
        cur = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
        kb = build_projects_page(allp, linked, cur.id if cur else None)
        if cb.message and isinstance(cb.message, Message) and cb.message.bot and cb.message.chat and cb.from_user:
            await show_panel(st, cb.message.bot, cb.message.chat.id, cb.from_user.id,
                             "Проекты:", kb)
    await cb.answer()

@router.callback_query(F.data.startswith("projects:link:"))
async def projects_link(cb: CallbackQuery):
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    _, _, pid = cb.data.split(":")
    async with session_scope() as st:
        await link_toggle_project(st, cb.from_user.id if cb.from_user else 0, int(pid))
        await st.commit()
        # перерисуем список
        allp = await list_projects(st)
        linked = set(await get_linked_project_ids(st, cb.from_user.id if cb.from_user else 0))
        cur = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
        kb = build_projects_page(allp, linked, cur.id if cur else None)
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
    # Проверяем, что сообщение существует перед попыткой редактирования
    if cb.message and isinstance(cb.message, Message) and hasattr(cb.message, 'edit_reply_markup'):
        try:
            await cb.message.edit_reply_markup(reply_markup=kb)
        except:
            pass
        await cb.message.answer("✅ Проект добавлен/удален из связанных", reply_markup=build_reply_kb(chat_on))
    await cb.answer("Готово")

@router.callback_query(F.data.startswith("projects:activate:"))
async def projects_activate(cb: CallbackQuery):
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    _, _, pid = cb.data.split(":")
    async with session_scope() as st:
        p = await st.get(Project, int(pid))
        if p:
            await set_active_project(st, cb.from_user.id if cb.from_user else 0, p)
            await st.commit()
            allp = await list_projects(st)
            linked = set(await get_linked_project_ids(st, cb.from_user.id if cb.from_user else 0))
            kb = build_projects_page(allp, linked, p.id)
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            # Проверяем, что сообщение существует перед попыткой редактирования
            if cb.message and isinstance(cb.message, Message) and hasattr(cb.message, 'edit_reply_markup'):
                try:
                    await cb.message.edit_reply_markup(reply_markup=kb)
                except:
                    pass
                await cb.message.answer(f"✅ Active: <b>{escape(p.name)}</b>", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

# Добавление проекта (без /project)
from aiogram.types import ForceReply

@router.callback_query(F.data == "projects:new")
async def projects_new(cb: CallbackQuery):
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    from app.db import session_scope
    if cb.message and isinstance(cb.message, Message):
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            await cb.message.answer("Название нового проекта:", reply_markup=ForceReply(selective=True))
    await cb.answer()

@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Название нового проекта:"))
async def projects_create(message: Message):
    from app.db import session_scope
    from app.handlers.keyboard import main_reply_kb as build_reply_kb
    from app.services.memory import get_chat_flags
    async with session_scope() as st:
        name = (message.text or "").strip()
        if not name:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            return await message.answer("Пустое имя. Попробуй ещё раз.", reply_markup=build_reply_kb(chat_on))
        # если есть helper get_or_create_project — используй его
        p = Project(name=name)
        st.add(p)
        await st.flush()
        await set_active_project(st, message.from_user.id if message.from_user else 0, p)
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        await message.answer(f"Проект создан и активирован: <b>{escape(name)}</b>", reply_markup=build_reply_kb(chat_on))


@router.callback_query(F.data == "mem:import_last")
async def mem_import_last(cb: CallbackQuery):
    return await wizard_import(cb)
