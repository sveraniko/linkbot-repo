from __future__ import annotations
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply, Message
from app.db import session_scope
from app.repo import repo_add, repo_list, repo_sync, repo_remove
from app.config import settings

router = Router()

def repo_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Add", callback_data="repo:add"),
        InlineKeyboardButton(text="📜 List", callback_data="repo:list"),
    ]])

@router.callback_query(F.data == "repo:open")
async def repo_open(cb: CallbackQuery):
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Repo:", reply_markup=repo_menu_kb())
    await cb.answer()

@router.callback_query(F.data == "repo:add")
async def repo_add_start(cb: CallbackQuery):
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Формат: <alias> <url> [branch]\nПример: lovender https://github.com/user/repo main",
                                reply_markup=ForceReply(selective=True))
    await cb.answer()

@router.message(F.reply_to_message & F.reply_to_message.text.startswith("Формат:"))
async def repo_add_reply(message: Message):
    parts = (message.text or "").splitlines()[-1].split()
    if len(parts) < 2:
        return await message.answer("Нужно: <alias> <url> [branch]")
    alias, url, *rest = parts
    branch = rest[0] if rest else "main"
    async with session_scope() as st:
        await repo_add(st, message.from_user.id if message.from_user else 0, alias, url, branch)
        await st.commit()
    await message.answer(f"Репозиторий добавлен: {alias} ({branch})")

@router.callback_query(F.data == "repo:list")
async def repo_list_open(cb: CallbackQuery):
    async with session_scope() as st:
        items = await repo_list(st, cb.from_user.id if cb.from_user else 0)
    if not items:
        if cb.message and isinstance(cb.message, Message):
            return await cb.message.answer("Список пуст. Нажми ➕ Add.")
    rows = []
    for r in items:
        rows.append([
            InlineKeyboardButton(text=f"{r.alias} ({r.branch})", callback_data="noop"),
            InlineKeyboardButton(text="Sync", callback_data=f"repo:sync:{r.alias}"),
            InlineKeyboardButton(text="Remove", callback_data=f"repo:rm:{r.alias}"),
        ])
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer("Репозитории:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()

@router.callback_query(F.data.startswith("repo:sync:"))
async def repo_sync_cb(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    alias = cb.data.split(":")[-1]
    token = getattr(settings, "github_token", None)
    async with session_scope() as st:
        out = await repo_sync(st, cb.from_user.id if cb.from_user else 0, alias, token=token)
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"<code>{out[:3500]}</code>", reply_markup=build_reply_kb(chat_on))
    await cb.answer()

@router.callback_query(F.data.startswith("repo:rm:"))
async def repo_rm_cb(cb: CallbackQuery):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    if not cb.data:
        return await cb.answer("Invalid data")
    alias = cb.data.split(":")[-1]
    async with session_scope() as st:
        await repo_remove(st, cb.from_user.id if cb.from_user else 0, alias)
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
    if cb.message and isinstance(cb.message, Message):
        await cb.message.answer(f"Удалён: {alias}", reply_markup=build_reply_kb(chat_on))
    await cb.answer()
