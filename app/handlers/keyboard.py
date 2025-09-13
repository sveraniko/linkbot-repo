from __future__ import annotations
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = Router()

BTN_ACTIONS = "⚙️ Actions"
BTN_CHAT_ON = "💬 Chat: ON"
BTN_CHAT_OFF= "💬 Chat: OFF"
BTN_STATUS  = "📊 Status"

def build_kb_minimal(chat_on: bool) -> ReplyKeyboardMarkup:
    chat_btn = KeyboardButton(text=BTN_CHAT_ON if chat_on else BTN_CHAT_OFF)
    row = [KeyboardButton(text=BTN_ACTIONS), chat_btn, KeyboardButton(text=BTN_STATUS)]
    return ReplyKeyboardMarkup(keyboard=[row], resize_keyboard=True, is_persistent=True)

@router.message(F.text == "/kb_on")
async def kb_on(message: Message):
    from app.db import session_scope
    from app.services.memory import get_chat_flags
    async with session_scope() as st:
        if message.from_user:
            chat_on, _, _, _ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Клавиатура включена.", reply_markup=build_kb_minimal(chat_on))

@router.message(F.text == BTN_ACTIONS)
async def kb_actions(message: Message):
    from app.handlers.menu import kb_menu  # импорт внутри, чтобы не ловить циклы
    from app.db import session_scope
    from app.services.memory import get_preferred_model
    async with session_scope() as st:
        if message.from_user:
            model = await get_preferred_model(st, message.from_user.id)
            await message.answer("Панель действий:", reply_markup=kb_menu(model))

@router.message(F.text == BTN_STATUS)
async def kb_status(message: Message):
    from app.handlers.status import render_status
    from app.db import session_scope
    async with session_scope() as st:
        if message.from_user:
            text = await render_status(st, message.from_user.id)
            await message.answer(text)

@router.message(F.text.in_({BTN_CHAT_ON, BTN_CHAT_OFF}))
async def kb_chat_toggle(message: Message):
    from app.services.memory import set_chat_mode
    from app.db import session_scope
    async with session_scope() as st:
        if message.from_user:
            new_state = await set_chat_mode(st, message.from_user.id, on=(message.text == BTN_CHAT_OFF))
            await st.commit()
            await message.answer(f"Chat mode: {'ON' if new_state else 'OFF'}", reply_markup=build_kb_minimal(new_state))