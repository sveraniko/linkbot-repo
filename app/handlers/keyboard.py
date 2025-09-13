from __future__ import annotations
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from app.db import get_session

router = Router()

# Кнопка "⚙️ Actions"
BTN_ACTIONS = "⚙️ Actions"
BTN_STATUS = "📊 Статус"
BTN_CHAT_ON = "💬 Chat ON"
BTN_CHAT_OFF = "🤫 Chat OFF"

def build_kb_minimal(chat_on: bool) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ACTIONS), KeyboardButton(text=BTN_CHAT_ON if not chat_on else BTN_CHAT_OFF), KeyboardButton(text=BTN_STATUS)],
        ],
        resize_keyboard=True
    )

@router.message(F.text == BTN_ACTIONS)
async def kb_actions(message: Message):
    from app.db import session_scope
    from app.services.memory import get_preferred_model
    from app.ui import show_panel
    from app.handlers.menu import kb_menu
    # Delete the original "⚙️ Actions" message to prevent chat clutter
    try:
        await message.delete()
    except:
        pass
    async with session_scope() as st:
        model = await get_preferred_model(st, message.from_user.id if message.from_user else 0)
        if message.bot and message.chat and message.from_user:
            await show_panel(st, message.bot, message.chat.id, message.from_user.id,
                             "Панель действий:", kb_menu(model))

@router.message(F.text == "/kb_on")
async def kb_on(message: Message):
    from app.db import session_scope
    from app.services.memory import get_chat_flags
    # Delete the original "/kb_on" message to prevent chat clutter
    try:
        await message.delete()
    except:
        pass
    async with session_scope() as st:
        if message.from_user:
            chat_on, _, _, _ = await get_chat_flags(st, message.from_user.id)
            await message.answer("Клавиатура включена.", reply_markup=build_kb_minimal(chat_on))

@router.message(F.text == BTN_STATUS)
async def kb_status(message: Message):
    from app.handlers.status import render_status
    from app.db import session_scope
    # Delete the original "📊 Статус" message to prevent chat clutter
    try:
        await message.delete()
    except:
        pass
    async with session_scope() as st:
        if message.from_user:
            text = await render_status(st, message.from_user.id)
            await message.answer(text)

@router.message(F.text.in_({BTN_CHAT_ON, BTN_CHAT_OFF}))
async def kb_chat_toggle(message: Message):
    from app.services.memory import set_chat_mode
    from app.db import session_scope
    # Delete the original chat toggle message to prevent chat clutter
    try:
        await message.delete()
    except:
        pass
    async with session_scope() as st:
        if message.from_user:
            new_state = await set_chat_mode(st, message.from_user.id, on=(message.text == BTN_CHAT_OFF))
            await st.commit()
            await message.answer(f"Chat mode: {'ON' if new_state else 'OFF'}", reply_markup=build_kb_minimal(new_state))