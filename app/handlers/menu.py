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
    link_toggle_project, set_active_project
)
from app.llm import ask_llm
from app.models import Project
from typing import List

router = Router()

def kb_menu(model: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="status:show"),
            InlineKeyboardButton(text="🧹 Сброс фильтров", callback_data="ctx:reset"),
        ],
        [
            InlineKeyboardButton(text="📄 Импорт (последний файл)", callback_data="wizard:import"),
            InlineKeyboardButton(text="🗜️ Импорт ZIP — подсказка", callback_data="hint:importzip"),
        ],
        [
            InlineKeyboardButton(text="📚 Sources", callback_data="sources:toggle"),
            InlineKeyboardButton(text="🎯 Scope", callback_data="scope:toggle"),
            InlineKeyboardButton(text="🤫 Quiet", callback_data="quiet:toggle"),
        ],
        [
            InlineKeyboardButton(text="📂 Projects", callback_data="projects:list"),
        ],
        [
            InlineKeyboardButton(text="🏷 API", callback_data="ctx:tags:api"),
            InlineKeyboardButton(text="DB", callback_data="ctx:tags:db"),
            InlineKeyboardButton(text="matching", callback_data="ctx:tags:matching"),
        ],
        [
            InlineKeyboardButton(text="📝 TODO", callback_data="ask:todo"),
            InlineKeyboardButton(text="⚠️ Risks", callback_data="ask:risks"),
            InlineKeyboardButton(text="📦 Release notes", callback_data="ask:relnotes"),
        ],
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
        await message.answer("Меню быстрых действий:", reply_markup=kb_menu(model))

@router.message(Command("actions"))
async def actions(message: Message):
    async with session_scope() as session:
        model = await get_preferred_model(session, message.from_user.id if message.from_user else 0)
        await message.answer("Панель действий:", reply_markup=kb_menu(model))

# ── Hints ───────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "hint:importzip")
async def hint_zip(cb: CallbackQuery):
    txt = (
        "Импорт ZIP:\n"
        "1) Прикрепите .zip как файл\n"
        "2) Ответом на сообщение отправьте:\n"
        "<code>/importzip tags code,snapshot,rev-YYYY-MM-DD</code>"
    )
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(txt)
    await cb.answer()

# ── Status (кнопка) ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "status:show")
async def status_show(cb: CallbackQuery):
    from app.db import session_scope
    from app.handlers.status import render_status
    async with session_scope() as st:
        text = await render_status(st, cb.from_user.id if cb.from_user else 0)
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(text)
    await cb.answer()

# ── Context presets ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ctx:reset")
async def ctx_reset(cb: CallbackQuery):
    from app.db import session_scope
    async with session_scope() as st:
        await set_context_filters(st, cb.from_user.id if cb.from_user else 0, kinds_csv="", tags_csv="")
        await st.commit()
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Фильтры контекста сброшены. Используется вся память проекта.")
    await cb.answer()

@router.callback_query(F.data.startswith("ctx:tags:"))
async def ctx_presets(cb: CallbackQuery):
    from app.db import session_scope
    if not cb.data:
        return await cb.answer("Invalid data")
    tag = cb.data.split(":")[-1]
    async with session_scope() as st:
        await set_context_filters(st, cb.from_user.id if cb.from_user else 0, tags_csv=tag)
        await st.commit()
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"Фильтры обновлены: tags={tag}")
    await cb.answer()

# ── Model switch ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("model:"))
async def model_switch(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    _, model = cb.data.split(":", 1)
    async with session_scope() as session:
        applied = await set_preferred_model(session, cb.from_user.id if cb.from_user else 0, model)
        await session.commit()
        if cb.message and isinstance(cb.message, Message) and hasattr(cb.message, 'edit_reply_markup'):
            try:
                await cb.message.edit_reply_markup(reply_markup=kb_menu(applied))
            except:
                pass
    await cb.answer(f"Модель установлена: {applied}")

# ── Import wizard (последний файл) ─────────────────────────────────────────────

@router.callback_query(F.data == "wizard:import")
async def do_import_last(cb: CallbackQuery):
    async with session_scope() as session:
        # чтобы избежать циклического импорта, берём функцию внутри хендлера
        from app.handlers.import_file import import_last_for_user
        if cb.message and isinstance(cb.message, Message):
            ok = await import_last_for_user(cb.message, session, tags=None)
            if not ok:
                await cb.message.answer("Нет «последнего файла». Пришлите .txt/.md/.json и повторите.")
    await cb.answer()

# ── Quick ASK templates ────────────────────────────────────────────────────────

async def _ask_with_template(cb: CallbackQuery, template: str):
    async with session_scope() as session:
        proj = await get_active_project(session, cb.from_user.id if cb.from_user else 0)
        if not proj:
            if cb.message and isinstance(cb.message, Message):
                await cb.message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
            return
        ctx = await gather_context(session, proj, user_id=cb.from_user.id if cb.from_user else 0, max_chunks=settings.project_max_chunks)
        model = await get_preferred_model(session, cb.from_user.id if cb.from_user else 0)
        answer = await ask_llm(template, ctx, model=model)
        if cb.message and isinstance(cb.message, Message):
            await cb.message.answer(answer)

@router.callback_query(F.data.startswith("ask:"))
async def ask_templates(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    kind = cb.data.split(":")[1]
    prompts = {
        "todo": "Сформируй подробный TODO-лист по проекту на ближайшую неделю на основе памяти.",
        "risks": "Определи ключевые риски проекта и предложи меры снижения. Ссылайся на контекст.",
        "relnotes": "Сгенерируй краткие release notes по последним изменениям и решениям, пригодные для команды.",
    }
    await _ask_with_template(cb, prompts.get(kind, "Сформируй краткий план работ по проекту."))
    await cb.answer()

# --- Quiet / Scope / Sources ---
@router.callback_query(F.data == "quiet:toggle")
async def quiet_toggle(cb: CallbackQuery):
    from app.db import session_scope
    from app.services.memory import get_chat_flags, set_quiet_mode
    async with session_scope() as st:
        _, quiet_on, _, _ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        newv = await set_quiet_mode(st, cb.from_user.id if cb.from_user else 0, on=not quiet_on)
        await st.commit()
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"Quiet mode: {'ON' if newv else 'OFF'}")
    await cb.answer()

@router.callback_query(F.data == "scope:toggle")
async def scope_toggle(cb: CallbackQuery):
    from app.db import session_scope
    from app.services.memory import toggle_scope
    async with session_scope() as st:
        newv = await toggle_scope(st, cb.from_user.id if cb.from_user else 0)
        await st.commit()
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"Scope: {newv}")
    await cb.answer()

# Новые обработчики для Sources
def build_sources_kb(current: str) -> InlineKeyboardMarkup:
    opts = ["active", "linked", "all", "global"]
    row = [InlineKeyboardButton(text=("• "+o if o==current else o), callback_data=f"sources:set:{o}") for o in opts]
    return InlineKeyboardMarkup(inline_keyboard=[row])

@router.callback_query(F.data == "sources:toggle")
async def sources_toggle(cb: CallbackQuery):
    async with session_scope() as st:
        from app.services.memory import get_chat_flags
        _, _, current, _ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
    kb = build_sources_kb(current)
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Выбери источники (Sources):", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("sources:set:"))
async def sources_set(cb: CallbackQuery):
    if not cb.data:
        return await cb.answer("Invalid data")
    _, _, val = cb.data.split(":")
    async with session_scope() as st:
        from app.services.memory import _ensure_user_state
        stt = await _ensure_user_state(st, cb.from_user.id if cb.from_user else 0)
        stt.sources_mode = val
        await st.commit()
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"Sources: {val}")
    await cb.answer()

# --- Projects list / link/unlink / activate ---
def build_projects_page(projects, linked_ids: set[int], active_id: int | None):
    rows = []
    rows.append([InlineKeyboardButton(text="➕ New project", callback_data="projects:new")])
    for p in projects:
        mark = "⭐ " if p.id in linked_ids else ""
        act = "✓ " if active_id == p.id else ""
        rows.append([
            InlineKeyboardButton(text=f"{act}{mark}{p.name}", callback_data="noop"),
            InlineKeyboardButton(text=("Unlink" if p.id in linked_ids else "Link"), callback_data=f"projects:link:{p.id}"),
            InlineKeyboardButton(text="Activate", callback_data=f"projects:activate:{p.id}")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data.startswith("projects:list"))
async def projects_list(cb: CallbackQuery):
    async with session_scope() as st:
        allp = await list_projects(st)
        linked = set(await get_linked_project_ids(st, cb.from_user.id if cb.from_user else 0))
        cur = await get_active_project(st, cb.from_user.id if cb.from_user else 0)
        kb = build_projects_page(allp, linked, cur.id if cur else None)
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Проекты:", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("projects:link:"))
async def projects_link(cb: CallbackQuery):
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
    # Проверяем, что сообщение существует перед попыткой редактирования
    if cb.message and isinstance(cb.message, Message) and hasattr(cb.message, 'edit_reply_markup'):
        try:
            await cb.message.edit_reply_markup(reply_markup=kb)
        except:
            pass
    await cb.answer("Готово")

@router.callback_query(F.data.startswith("projects:activate:"))
async def projects_activate(cb: CallbackQuery):
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
            # Проверяем, что сообщение существует перед попыткой редактирования
            if cb.message and isinstance(cb.message, Message) and hasattr(cb.message, 'edit_reply_markup'):
                try:
                    await cb.message.edit_reply_markup(reply_markup=kb)
                except:
                    pass
                await cb.message.answer(f"✅ Active: <b>{p.name}</b>")
    await cb.answer()

# Добавление проекта (без /project)
from aiogram.types import ForceReply

@router.callback_query(F.data == "projects:new")
async def projects_new(cb: CallbackQuery):
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Название нового проекта:", reply_markup=ForceReply(selective=True))
    await cb.answer()

@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Название нового проекта:"))
async def projects_create(message: Message):
    async with session_scope() as st:
        name = (message.text or "").strip()
        if not name:
            return await message.answer("Пустое имя. Попробуй ещё раз.")
        # если есть helper get_or_create_project — используй его
        p = Project(name=name)
        st.add(p)
        await st.flush()
        await set_active_project(st, message.from_user.id if message.from_user else 0, p)
        await st.commit()
    await message.answer(f"Проект создан и активирован: <b>{name}</b>")