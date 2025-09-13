from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.services.memory import get_active_project, gather_context, get_preferred_model
from app.config import settings
from app.llm import ask_llm

router = Router()

@router.message(Command("ask"))
async def ask(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    proj = await get_active_project(st, message.from_user.id)
    if not proj:
        await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return
    q = message.text.split(maxsplit=1)
    if len(q) < 2:
        await message.answer("Формат: /ask <вопрос>")
        return
    question = q[1].strip()
    ctx = await gather_context(st, proj, user_id=message.from_user.id, max_chunks=settings.project_max_chunks)
    model = await get_preferred_model(st, message.from_user.id)
    answer = await ask_llm(question, ctx, model=model)
    await message.answer(answer)