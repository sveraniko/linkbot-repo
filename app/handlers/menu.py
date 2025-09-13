# app/handlers/menu.py
from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.config import settings
from app.services.memory import (
    get_active_project, set_context_filters,
    get_preferred_model, set_preferred_model, gather_context
)
from app.llm import ask_llm

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
            InlineKeyboardButton(text="📂 Projects", callback_data="projects:list:1"),
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
async def menu(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    model = await get_preferred_model(st, message.from_user.id)
    await message.answer("Меню быстрых действий:", reply_markup=kb_menu(model))

@router.message(Command("actions"))
async def actions(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    model = await get_preferred_model(st, message.from_user.id)
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
    await cb.message.answer(txt)
    await cb.answer()

# ── Status (кнопка) ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "status:show")
async def status_show(cb: CallbackQuery):
    from app.db import session_scope
    from app.handlers.status import render_status
    async with session_scope() as st:
        text = await render_status(st, cb.from_user.id)
    await cb.message.answer(text)
    await cb.answer()

# ── Context presets ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ctx:reset")
async def ctx_reset(cb: CallbackQuery):
    from app.db import session_scope
    async with session_scope() as st:
        await set_context_filters(st, cb.from_user.id, kinds_csv="", tags_csv="")
        await st.commit()
    await cb.message.answer("Фильтры контекста сброшены. Используется вся память проекта.")
    await cb.answer()

@router.callback_query(F.data.startswith("ctx:tags:"))
async def ctx_presets(cb: CallbackQuery):
    from app.db import session_scope
    tag = cb.data.split(":")[-1]
    async with session_scope() as st:
        await set_context_filters(st, cb.from_user.id, tags_csv=tag)
        await st.commit()
    await cb.message.answer(f"Фильтры обновлены: tags={tag}")
    await cb.answer()

# ── Model switch ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("model:"))
async def model_switch(cb: CallbackQuery, session: AsyncSession = get_session()):
    _, model = cb.data.split(":", 1)
    st = await anext(session)
    applied = await set_preferred_model(st, cb.from_user.id, model)
    await st.commit()
    await cb.message.edit_reply_markup(reply_markup=kb_menu(applied))
    await cb.answer(f"Модель установлена: {applied}")

# ── Import wizard (последний файл) ─────────────────────────────────────────────

@router.callback_query(F.data == "wizard:import")
async def do_import_last(cb: CallbackQuery, session: AsyncSession = get_session()):
    st = await anext(session)
    # чтобы избежать циклического импорта, берём функцию внутри хендлера
    from app.handlers.import_file import import_last_for_user
    ok = await import_last_for_user(cb.message, st, tags=None)
    if not ok:
        await cb.message.answer("Нет «последнего файла». Пришлите .txt/.md/.json и повторите.")
    await cb.answer()

# ── Quick ASK templates ────────────────────────────────────────────────────────

async def _ask_with_template(cb: CallbackQuery, template: str, st: AsyncSession):
    proj = await get_active_project(st, cb.from_user.id)
    if not proj:
        await cb.message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return
    ctx = await gather_context(st, proj, user_id=cb.from_user.id, max_chunks=settings.project_max_chunks)
    model = await get_preferred_model(st, cb.from_user.id)
    answer = await ask_llm(template, ctx, model=model)
    await cb.message.answer(answer)

@router.callback_query(F.data.startswith("ask:"))
async def ask_templates(cb: CallbackQuery, session: AsyncSession = get_session()):
    st = await anext(session)
    kind = cb.data.split(":")[1]
    prompts = {
        "todo": "Сформируй подробный TODO-лист по проекту на ближайшую неделю на основе памяти.",
        "risks": "Определи ключевые риски проекта и предложи меры снижения. Ссылайся на контекст.",
        "relnotes": "Сгенерируй краткие release notes по последним изменениям и решениям, пригодные для команды.",
    }
    await _ask_with_template(cb, prompts.get(kind, "Сформируй краткий план работ по проекту."), st)
    await cb.answer()

# --- Quiet / Scope / Sources ---
@router.callback_query(F.data == "quiet:toggle")
async def quiet_toggle(cb: CallbackQuery):
    from app.db import session_scope
    from app.services.memory import get_chat_flags, set_quiet_mode
    async with session_scope() as st:
        _, quiet_on, _, _ = await get_chat_flags(st, cb.from_user.id)
        newv = await set_quiet_mode(st, cb.from_user.id, on=not quiet_on)
        await st.commit()
    await cb.message.answer(f"Quiet mode: {'ON' if newv else 'OFF'}")
    await cb.answer()

@router.callback_query(F.data == "scope:toggle")
async def scope_toggle(cb: CallbackQuery):
    from app.db import session_scope
    from app.services.memory import toggle_scope
    async with session_scope() as st:
        newv = await toggle_scope(st, cb.from_user.id)
        await st.commit()
    await cb.message.answer(f"Scope: {newv}")
    await cb.answer()

@router.callback_query(F.data == "sources:toggle")
async def sources_toggle(cb: CallbackQuery):
    from app.db import session_scope
    from app.services.memory import toggle_sources
    async with session_scope() as st:
        newv = await toggle_sources(st, cb.from_user.id)
        await st.commit()
    await cb.message.answer(f"Sources: {newv}")
    await cb.answer()

# --- Projects list / link/unlink / activate (MVP, пагинация по 6) ---
def build_projects_page(projects, linked_ids: set[int], active_id: int | None, page: int, pages: int):
    rows = []
    for p in projects:
        mark = "⭐ " if p.id in linked_ids else ""
        act = "✓ " if active_id == p.id else ""
        rows.append([InlineKeyboardButton(text=f"{act}{mark}{p.name}", callback_data=f"projects:noop"),
                     InlineKeyboardButton(text=("Unlink" if p.id in linked_ids else "Link"), callback_data=f"projects:link:{p.id}:{page}"),
                     InlineKeyboardButton(text="Activate", callback_data=f"projects:activate:{p.id}:{page}")])
    nav = []
    if page > 1: nav.append(InlineKeyboardButton(text="⟵ Prev", callback_data=f"projects:list:{page-1}"))
    if page < pages: nav.append(InlineKeyboardButton(text="Next ⟶", callback_data=f"projects:list:{page+1}"))
    if nav: rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data.startswith("projects:list:"))
async def projects_list(cb: CallbackQuery, session: AsyncSession = get_session()):
    from math import ceil
    from app.services.memory import list_projects, get_linked_project_ids, get_active_project
    st = await anext(session)
    page = int(cb.data.split(":")[-1]); per = 6
    allp = await list_projects(st)
    linked = set(await get_linked_project_ids(st, cb.from_user.id))
    proj = await get_active_project(st, cb.from_user.id)
    pages = max(1, ceil(len(allp)/per))
    slice_ = allp[(page-1)*per: page*per]
    kb = build_projects_page(slice_, linked, proj.id if proj else None, page, pages)
    await cb.message.answer("Проекты:", reply_markup=kb); await cb.answer()

@router.callback_query(F.data.startswith("projects:link:"))
async def projects_link(cb: CallbackQuery, session: AsyncSession = get_session()):
    _, _, pid, page = cb.data.split(":")
    from app.services.memory import link_toggle_project, list_projects, get_linked_project_ids, get_active_project
    st = await anext(session)
    linked_now = await link_toggle_project(st, cb.from_user.id, int(pid))
    await st.commit()
    # Перерисуем текущую страницу
    return await projects_list(CallbackQuery(id=cb.id, from_user=cb.from_user, message=cb.message, chat_instance=cb.chat_instance, data=f"projects:list:{page}"), session)

@router.callback_query(F.data.startswith("projects:activate:"))
async def projects_activate(cb: CallbackQuery, session: AsyncSession = get_session()):
    _, _, pid, page = cb.data.split(":")
    from app.services.memory import set_active_project
    from app.models import Project
    st = await anext(session)
    # тут можно просто set_active_project по id; у тебя может быть функция set_active_by_id
    project = await st.get(Project, int(pid))
    await set_active_project(st, cb.from_user.id, project)
    await st.commit()
    await cb.message.answer(f"Активный проект установлен: <b>{project.name}</b>")
    await cb.answer()
