from __future__ import annotations
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.services.memory import get_active_project, gather_context, get_preferred_model
from app.llm import ask_llm
from app.handlers.keyboard import build_reply_kb
from app.services.memory import get_chat_flags
from app.config import settings

router = Router()

@router.message(Command("ask"))
async def ask(message: Message):
    from app.db import session_scope
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id if message.from_user else 0)
        if not proj:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>", reply_markup=build_reply_kb(chat_on))
            return
        q = (message.text or "").split(maxsplit=1)
        if len(q) < 2:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            await message.answer("Формат: /ask <вопрос>", reply_markup=build_reply_kb(chat_on))
            return
        question = q[1].strip()
        ctx = await gather_context(st, proj, user_id=message.from_user.id if message.from_user else 0, max_chunks=settings.project_max_chunks)
        model = await get_preferred_model(st, message.from_user.id if message.from_user else 0)
        answer = await ask_llm(question, ctx, model=model)
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        await message.answer(answer, reply_markup=build_reply_kb(chat_on))