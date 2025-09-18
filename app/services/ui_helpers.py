"""UI helpers (Telegram-agnostic wrappers to be used from handlers)
- show_answer_with_bar(bot, chat_id, message_id, text, bar)
- restore_bar_only(cb, run_id)
- attach_reply_kb(message, user_id)
"""
from __future__ import annotations
from typing import Optional
from aiogram.types import Message
from app.db import session_scope
from app.handlers.keyboard import main_reply_kb
from app.services.memory import get_chat_flags


async def attach_reply_kb(message: Message, user_id: int) -> None:
    async with session_scope() as st:
        chat_on, *_ = await get_chat_flags(st, user_id)
        await message.answer("", reply_markup=main_reply_kb(chat_on))
