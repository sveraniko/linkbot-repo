"""Telegram utility functions for message handling and cleanup."""
import asyncio
import logging
from typing import List, Union
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

# Get TTL from environment or use default
import os
UI_CLEANUP_TTL = int(os.getenv("UI_CLEANUP_TTL", "4"))

async def _toast(cb: CallbackQuery, text: str, show_alert: bool = False) -> None:
    """Send a toast message via answerCallbackQuery."""
    try:
        await cb.answer(text, show_alert=show_alert)
    except Exception as e:
        logger.warning(f"Failed to send toast message: {e}")

async def _safe_delete(bot: Bot, chat_id: int, msg_ids: Union[int, List[int]]) -> None:
    """Safely delete messages, suppressing NotFound errors."""
    if isinstance(msg_ids, int):
        msg_ids = [msg_ids]
    
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            # Suppress NotFound errors but log others
            if "message to delete not found" not in str(e).lower():
                logger.warning(f"Failed to delete message {msg_id}: {e}")

async def _send_ephemeral(bot: Bot, chat_id: int, text: str, ttl: int = UI_CLEANUP_TTL) -> None:
    """Send an ephemeral message that self-destructs after TTL seconds."""
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text)
        # Schedule deletion after TTL seconds
        asyncio.create_task(_delayed_delete(bot, chat_id, msg.message_id, ttl))
    except Exception as e:
        logger.warning(f"Failed to send ephemeral message: {e}")

async def _delayed_delete(bot: Bot, chat_id: int, msg_id: int, delay: int) -> None:
    """Delete a message after a delay."""
    try:
        await asyncio.sleep(delay)
        await _safe_delete(bot, chat_id, msg_id)
    except Exception as e:
        logger.warning(f"Failed to delete message after delay: {e}")