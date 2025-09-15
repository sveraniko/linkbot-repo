from __future__ import annotations
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from app.db import session_scope
from app.services.memory import get_chat_flags, set_chat_mode

router = Router(name="keyboard")

BTN_ACTIONS = "⚙️ Actions"
BTN_CHAT_ON = "💬 Chat: ON"
BTN_CHAT_OFF = "😴 Chat: OFF"
BTN_ASK = "ASK-WIZARD ❓"
BTN_MEMORY = "🧠 Memory"

SERVICE_TEXTS = {BTN_ACTIONS, BTN_ASK, BTN_CHAT_ON, BTN_CHAT_OFF, BTN_MEMORY, "Меню", "Menu", "Chat"}

def main_reply_kb(chat_on: bool) -> ReplyKeyboardMarkup:
    chat_label = BTN_CHAT_ON if chat_on else BTN_CHAT_OFF
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[[
            KeyboardButton(text=BTN_ACTIONS),
            KeyboardButton(text=chat_label),
            KeyboardButton(text=BTN_ASK),
        ], [
            KeyboardButton(text=BTN_MEMORY),  # Add Memory button to bottom row
        ]],
    )

@router.message(F.text == BTN_ACTIONS)
async def open_actions_from_kb(message: Message):
    """
    Открывает твою панель действий по кнопке снизу.
    Пробуем найти одну из функций меню и вызвать её напрямую.
    """
    open_menu = None
    try:
        # 1) если у тебя есть такой модуль/ф-ция
        from app.handlers.menu import menu as open_menu  # noqa: F401
    except Exception:
        try:
            from app.handlers.menu import actions as open_menu  # noqa: F401
        except Exception:
            open_menu = None

    if open_menu:
        return await open_menu(message)

    # Фоллбек, если прямой вызов не найден:
    # лучше вернуть подсказку, чем молчать
    return await message.answer("Открой меню командой /menu")

@router.message(F.text.in_({BTN_CHAT_ON, BTN_CHAT_OFF}))
async def kb_chat_toggle(message: Message):
    """Toggle Chat ON/OFF and rebuild the bottom-row keyboard. Never call LLM here."""
    if not message.from_user:
        return
    async with session_scope() as st:
        chat_on, *_ = await get_chat_flags(st, message.from_user.id)
        new_state = not chat_on
        await set_chat_mode(st, message.from_user.id, on=new_state)
        await st.commit()
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(
            f"Чат: {'ON' if new_state else 'OFF'}",
            reply_markup=main_reply_kb(new_state),
        )

@router.message(F.text == BTN_MEMORY)
async def open_memory_from_kb(message: Message):
    """
    Открывает панель Memory по кнопке снизу.
    """
    from app.handlers.memory_panel import memory_open
    return await memory_open(message)

__all__ = ["main_reply_kb", "SERVICE_TEXTS", "BTN_ACTIONS", "BTN_CHAT_ON", "BTN_CHAT_OFF", "BTN_ASK", "BTN_MEMORY", "router"]