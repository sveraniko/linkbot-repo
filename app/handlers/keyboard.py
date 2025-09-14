from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from app.db import session_scope
from app.services.memory import get_chat_flags, set_chat_mode

router = Router()

# Кнопка "⚙️ Actions"
BTN_ACTIONS = "⚙️ Actions"
BTN_STATUS = "📊 Status"

def CHAT_LABEL(on: bool) -> str:
    return "💬 Chat: ON" if on else "💤 Chat: OFF"

def build_reply_kb(chat_on: bool) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ACTIONS),
             KeyboardButton(text=CHAT_LABEL(chat_on)),
             KeyboardButton(text=BTN_STATUS)]
        ],
        resize_keyboard=True
    )

@router.message(Command("start"))
async def cmd_start(message: Message):
    async with session_scope() as st:
        chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
    await message.answer(
        "Привет! Клава внизу активна.",
        reply_markup=build_reply_kb(chat_on)
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
            await message.answer("Клавиатура включена.", reply_markup=build_reply_kb(chat_on))

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

# Тумблер Chat — отрабатывает на оба текста (ON/OFF)
@router.message(F.text.in_({"💬 Chat: ON", "💤 Chat: OFF", "Chat", "Чат"}))
async def kb_chat_toggle(message: Message):
    from app.services.memory import set_chat_mode, get_chat_flags
    from app.db import session_scope
    # Delete the original chat toggle message to prevent chat clutter
    try:
        await message.delete()
    except:
        pass
    async with session_scope() as st:
        if message.from_user:
            chat_on, _, _, _ = await get_chat_flags(st, message.from_user.id)
            new_on = not chat_on                 # <-- ВАЖНО: инвертируем
            await set_chat_mode(st, message.from_user.id, on=new_on)
            await st.commit()
            await message.answer(f"Chat mode: {'ON' if new_on else 'OFF'}", reply_markup=build_reply_kb(new_on))  # <-- пересобираем клавиатуру