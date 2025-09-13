from __future__ import annotations
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.memory import _ensure_user_state

async def show_panel(st: AsyncSession, bot: Bot, chat_id: int, user_id: int, text: str, kb: InlineKeyboardMarkup):
    """
    Удаляет предыдущую «панель» пользователя и присылает новую.
    Возвращает объект Message (aiogram).
    """
    stt = await _ensure_user_state(st, user_id)
    old_id = stt.last_panel_msg_id
    if old_id:
        try:
            await bot.delete_message(chat_id, old_id)
        except Exception:
            # мог быть уже удалён/очищен — игнорируем
            pass

    sent = await bot.send_message(chat_id, text, reply_markup=kb)
    stt.last_panel_msg_id = sent.message_id
    await st.commit()
    return sent