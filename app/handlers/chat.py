from __future__ import annotations
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.config import settings
from app.services.memory import (
    get_active_project, get_chat_flags, get_context_filters_state, get_linked_project_ids,
    gather_context_sources, get_preferred_model,
)
from app.models import BotMessage
from app.llm import ask_llm
from html import escape

router = Router()

# Префикс для запроса тегов
TAG_PROMPT_PREFIX = "Свои теги (через запятую)"

def answer_kb(msg_id: int, saved: bool):
    # обязательные кнопки: Save / Summary / Tag / Delete / Refine
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=("✅ Saved" if saved else "💾 Save"), callback_data=f"ans:save:{msg_id}"),
        InlineKeyboardButton(text="📌 Summary", callback_data=f"ans:sum:{msg_id}"),
        InlineKeyboardButton(text="🏷 Tag", callback_data=f"ans:tag:{msg_id}"),
    ],[
        InlineKeyboardButton(text="🗑 Delete", callback_data=f"ans:del:{msg_id}"),
        InlineKeyboardButton(text="↩ Refine", callback_data=f"ans:ref:{msg_id}"),
    ]])

@router.message(F.text & ~F.text.startswith("/"))
async def on_free_text(message: Message):
    from app.db import session_scope
    async with session_scope() as st:
        if not message.from_user:
            return
        
        # Если это ответ на наш форс-промпт тегов — пропускаем,
        # пусть обработает tags_free_reply
        if message.reply_to_message and (
            (message.reply_to_message.text or "").startswith(TAG_PROMPT_PREFIX) or
            (message.reply_to_message.text or "").startswith("Свои теги для импорта")
        ):
            return

        chat_on, quiet_on, sources_mode, scope_mode = await get_chat_flags(st, message.from_user.id)
        if quiet_on:
            return
        if not chat_on:
            # один мягкий хинт можно запоминать в памяти, но для MVP просто ответим
            return await message.answer("💡 Включи чат: нажми «💬 Chat» на синей клавиатуре или команду /actions → Quiet OFF.")
        proj = await get_active_project(st, message.from_user.id)
        kinds, tags = await get_context_filters_state(st, message.from_user.id)
        linked_ids = await get_linked_project_ids(st, message.from_user.id)

        # Сбор контекста по источникам (упрощённая версия)
        ctx_texts, used_pids = await gather_context_sources(
            st, proj, message.from_user.id,
            max_chunks=settings.project_max_chunks,
            kinds=kinds, tags=tags,
            sources_mode=sources_mode, linked_ids=linked_ids,
        )

        model = await get_preferred_model(st, message.from_user.id)

        # Решаем по scope:
        prompt = message.text or ""
        final_ctx = ctx_texts if scope_mode in ("auto", "project") else []
        
        # Проверяем, нужен ли проект
        if not proj and sources_mode in ("active", "linked") and scope_mode != "global":
            await message.answer("Сначала выбери проект: открой Actions → Projects (или /project <name>). "
                                 "Либо поставь Sources=Global в Actions.")
            return
        
        answer = await ask_llm(prompt, final_ctx, model=model)  # внутри ask_llm ты уже умеешь сшивать ctx в system

        # штамп
        from app.services.memory import list_projects
        names = {p.id: p.name for p in await list_projects(st)}
        stamp = f"\n\n<i>Project: {escape(proj.name) if proj else '—'} • Scope: {scope_mode} • Sources: {sources_mode} • Model: {model}</i>"

        sent = await message.answer(answer + stamp)
        bm = BotMessage(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            tg_message_id=sent.message_id,
            reply_to_user_msg_id=message.message_id,
            artifact_id=None, saved=False,
            project_id=(proj.id if proj else None),
            used_projects={"ids": used_pids},
        )
        st.add(bm); await st.commit()
        await sent.edit_reply_markup(reply_markup=answer_kb(sent.message_id, saved=False))