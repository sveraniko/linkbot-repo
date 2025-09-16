from __future__ import annotations
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
import sqlalchemy as sa
from sqlalchemy import distinct, and_
from typing import Iterable, Sequence
from html import escape
import re
import datetime as dt
from zoneinfo import ZoneInfo
import zipfile
import tempfile
from pathlib import Path

from app.db import session_scope
from app.models import Artifact, Chunk, UserState, Tag, artifact_tags
from app.services.memory import _ensure_user_state, get_active_project, get_chat_flags, get_linked_project_ids
from app.handlers.keyboard import main_reply_kb
from app.handlers.import_file import _LAST_DOC
from app.services.artifacts import create_import
from app.config import settings
from app.storage import save_file
from app.ignore import load_pmignore, iter_text_files
from app.utils.zipfix import fix_zip_name, decode_text_bytes
from app.utils.tg import _toast, _safe_delete, _send_ephemeral  # Add this import

# Add Berlin timezone
BERLIN = ZoneInfo("Europe/Berlin")

router = Router(name="ask")

# -------------------- Utils
def _ids_get(stt: UserState) -> list[int]:
    raw = (stt.selected_artifact_ids or "").strip()
    if not raw:
        return []
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids

def _ids_set(stt: UserState, ids: Iterable[int]) -> None:
    uniq = sorted({int(i) for i in ids})
    stt.selected_artifact_ids = ",".join(str(i) for i in uniq) if uniq else None

def _budget_for_selection(chunks: Sequence[Chunk]) -> int:
    total = 0
    for ch in chunks:
        try:
            token_count = int(ch.tokens or 0)
            # Ensure we don't have negative token counts
            total += max(0, token_count)
        except Exception:
            # Fallback estimation if tokens is not a valid number
            total += max(1, len(ch.text)//4)
    return total

def _format_selected_summary(artifacts: list[Artifact]) -> str:
    """Format a summary of selected artifacts for display at the top of the panel."""
    if not artifacts:
        return ""
    
    # Show first artifact with its title
    first = artifacts[0]
    title = (first.title or str(first.id))[:50]
    summary = f"Выбрано: {len(artifacts)} • {escape(title)} [{first.id}]"
    
    # If more than one artifact, show count of remaining
    if len(artifacts) > 1:
        summary += f" … + ещё {len(artifacts) - 1}"
    
    return summary

def _panel_kb(selected_count: int, budget_label: str, auto_clear: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    # Step 1: Search is always available
    b.button(text="📋 List", callback_data="aw:list")
    # Step 2: Ask appears only when at least one source is selected
    if selected_count > 0:
        b.button(text="❓ Ask", callback_data="aw:arm")
    b.adjust(2)
    # Row 2
    b.button(text=f"Auto-clear: {'ON' if auto_clear else 'OFF'}", callback_data="aw:autoclear")
    b.button(text="❌ Сброс", callback_data="aw:clear")
    b.adjust(2)
    # Row 3: Import last button
    b.button(text="📥 Import last", callback_data="aw:import_last")
    # Row 4 (label, non-interactive)
    b.button(text=budget_label or "Бюджет: ~0 токенов", callback_data="aw:noop")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()

async def _calc_budget_label(st, sel: list[int]) -> str:
    if not sel:
        return "Бюджет: ~0 токенов"
    q = sa.select(Chunk).where(Chunk.artifact_id.in_(sel))
    chunks = (await st.execute(q)).scalars().all()
    return f"Бюджет: ~{_budget_for_selection(chunks)} токенов"

def _parse_search_query(q: str) -> tuple[list[str], list[str], str | None]:
    """
    Parse search query to extract tag, id, and title filters.
    Returns (tag_filters, id_filters, title_filter)
    """
    # Strip whitespace
    q = q.strip()
    
    # Handle special case: if input is purely numeric, treat as ID search (Hotfix C)
    if re.match(r'^\d+$', q):
        return [], [q], None
    
    # Handle special case: if input starts with #, treat as tag search (Hotfix C)
    if q.startswith('#'):
        tag_query = q[1:].strip()  # Remove the # prefix
        if tag_query:
            return [tag_query], [], None
        return [], [], None
    
    # Otherwise, treat as title search
    return [], [], q

async def _render_panel(m: Message, st, q: str | None = None, page: int = 1, user_id: int | None = None):
    # Use provided user_id if available, otherwise fallback to m.from_user.id
    actual_user_id = user_id if user_id is not None else (m.from_user.id if m.from_user else None)
    if not actual_user_id:
        return
    proj = await get_active_project(st, actual_user_id)
    print(f"DEBUG: proj: {proj}")
    if not proj:
        print(f"DEBUG: No active project found for user {actual_user_id}")
        # Always include reply keyboard to prevent it from disappearing
        chat_on, *_ = await get_chat_flags(st, actual_user_id)
        await m.answer("Нет активного проекта. Создай или выбери.", reply_markup=main_reply_kb(chat_on))
        return
    stt = await _ensure_user_state(st, actual_user_id)
    sel = set(_ids_get(stt))
    
    # Get linked project IDs for proper search scope (Hotfix B)
    linked_project_ids = await get_linked_project_ids(st, actual_user_id)
    # Combine active project ID with linked project IDs
    project_ids = [proj.id] + linked_project_ids
    
    # Log parameters for debugging (Hotfix A)
    print(f"DEBUG: _render_panel - project_ids={project_ids}, search_query='{q}', page={page}")
    
    # Get selected artifacts for summary display
    selected_artifacts = []
    if sel:
        sel_query = sa.select(Artifact).where(Artifact.id.in_(list(sel)))
        selected_artifacts = (await st.execute(sel_query)).scalars().all()

    # Build base query using subqueries to avoid DISTINCT ON issues (Variant B approach) (Hotfix D)
    if q:
        # Parse search query for different filter types
        tag_filters, id_filters, title_filter = _parse_search_query(q)
        
        # Create subquery for artifact IDs based on search criteria
        if tag_filters:
            # Subquery for tag-based search using join and distinct (Variant B) (Hotfix D)
            tag_subq = (
                sa.select(sa.distinct(artifact_tags.c.artifact_id))
                .join(Tag, artifact_tags.c.tag_name == Tag.name)
                .where(
                    sa.and_(
                        artifact_tags.c.artifact_id == Artifact.id,  # Join condition in subquery
                        Artifact.project_id.in_(project_ids),
                        sa.func.lower(Tag.name).like(f"%{tag_filters[0].lower()}%")
                    )
                )
            )
            # Main query using the subquery (Variant B) (Hotfix D)
            base = sa.select(Artifact).where(
                Artifact.id.in_(tag_subq)
            ).order_by(Artifact.created_at.desc())
        elif id_filters:
            # Direct ID match (Variant B) (Hotfix D)
            try:
                artifact_id = int(id_filters[0])
                # Subquery for ID-based search (Variant B) (Hotfix D)
                id_subq = sa.select(Artifact.id).where(
                    sa.and_(
                        Artifact.project_id.in_(project_ids),
                        Artifact.id == artifact_id
                    )
                )
                # Main query using the subquery (Variant B) (Hotfix D)
                base = sa.select(Artifact).where(
                    Artifact.id.in_(id_subq)
                ).order_by(Artifact.created_at.desc())
            except ValueError:
                # If ID is not valid, return empty result
                base = sa.select(Artifact).where(
                    Artifact.id.is_(None)  # This will return empty result
                ).order_by(Artifact.created_at.desc())
        elif title_filter:
            # Title-based search (Variant B) (Hotfix D)
            # Subquery for title-based search (Variant B) (Hotfix D)
            title_subq = sa.select(Artifact.id).where(
                sa.and_(
                    Artifact.project_id.in_(project_ids),
                    sa.func.lower(Artifact.title).like(f"%{title_filter.lower()}%")
                )
            )
            # Main query using the subquery (Variant B) (Hotfix D)
            base = sa.select(Artifact).where(
                Artifact.id.in_(title_subq)
            ).order_by(Artifact.created_at.desc())
        else:
            # No specific filter, show all artifacts (Variant B) (Hotfix D)
            # Subquery for all artifacts (Variant B) (Hotfix D)
            all_subq = sa.select(Artifact.id).where(
                Artifact.project_id.in_(project_ids)
            )
            # Main query using the subquery (Variant B) (Hotfix D)
            base = sa.select(Artifact).where(
                Artifact.id.in_(all_subq)
            ).order_by(Artifact.created_at.desc())
    else:
        # No search query, show all artifacts (Variant B) (Hotfix D)
        # Subquery for all artifacts (Variant B) (Hotfix D)
        all_subq = sa.select(Artifact.id).where(
            Artifact.project_id.in_(project_ids)
        )
        # Main query using the subquery (Variant B) (Hotfix D)
        base = sa.select(Artifact).where(
            Artifact.id.in_(all_subq)
        ).order_by(Artifact.created_at.desc())
    
    page_size = 5  # Changed to 5 items per page as per SPEC v2
    # Execute query with pagination
    res = (await st.execute(base.limit(page_size).offset((page-1)*page_size))).scalars().all()
    
    # Additional uniqueness check in Python as a safety measure (Hotfix D)
    unique_res = []
    seen_ids = set()
    for artifact in res:
        if artifact.id not in seen_ids:
            unique_res.append(artifact)
            seen_ids.add(artifact.id)
    res = unique_res

    # Send header with search functionality
    if m.bot:
        # Header message with search button
        header_builder = InlineKeyboardBuilder()
        header_builder.button(text="🔍", callback_data="aw:search")
        header_text = "Найти источник"
        
        # If there's an active search, show search chip
        if q:
            header_text = f'Поиск: "{q}"'
            header_builder.button(text="✖ Очистить", callback_data="aw:clear_search")
            header_builder.adjust(2)
        else:
            header_builder.adjust(1)
            
        # Send header message
        header_msg = await m.bot.send_message(
            chat_id=m.chat.id,
            text=header_text,
            reply_markup=header_builder.as_markup()
        )
        header_msg_id = header_msg.message_id

    # Send each artifact as a separate message with its own inline keyboard
    if m.bot:
        # Delete previous messages if this is a pagination action
        if stt.ask_page_msg_ids:
            try:
                msg_ids = [int(id_str) for id_str in stt.ask_page_msg_ids.split(',') if id_str.strip()]
                for msg_id in msg_ids:
                    await _safe_delete(m.bot, m.chat.id, msg_id)
            except Exception:
                pass  # Ignore errors when deleting messages
        
        # Delete previous footer message if it exists
        if stt.ask_footer_msg_id:
            try:
                await _safe_delete(m.bot, m.chat.id, stt.ask_footer_msg_id)
            except Exception:
                pass  # Ignore errors when deleting messages
        
        # Send new messages
        sent_msg_ids = []
        for i, a in enumerate(res, 1):
            # Create artifact text line in required format (N. <Название> [#tag1 #tag2] (id 689..., 2025-09-16))
            tags = ""
            if a.tags:
                tag_list = [t.name for t in a.tags]
                if tag_list:
                    tags = " [" + " ".join(escape(t) for t in tag_list[:3]) + "]"
            title = escape((a.title or str(a.id))[:80])
            created_at = a.created_at.strftime("%Y-%m-%d") if a.created_at else ""
            text_line = f"{i}. {title}{tags} (id {a.id}{', ' + created_at if created_at else ''})"
            
            # Create inline keyboard for this artifact
            kb = InlineKeyboardBuilder()
            mark = "✅" if a.id in sel else "➕"
            kb.button(text=mark, callback_data=f"aw:toggle:{a.id}")
            kb.button(text="🗑", callback_data=f"aw:delete:{a.id}")
            kb.adjust(2)
            
            # Send message for this artifact
            sent_msg = await m.bot.send_message(
                chat_id=m.chat.id,
                text=text_line,
                reply_markup=kb.as_markup()
            )
            sent_msg_ids.append(str(sent_msg.message_id))
        
        # Store message IDs for future pagination
        stt.ask_page_msg_ids = ",".join(sent_msg_ids) if sent_msg_ids else None
        
        # Send pagination footer
        # Get total count for pagination using subquery approach (Variant B) (Hotfix D)
        count_subq = sa.select(sa.func.count(Artifact.id)).where(Artifact.project_id.in_(project_ids))
        # Apply filters if there's a search query
        if q:
            tag_filters, id_filters, title_filter = _parse_search_query(q)
            if tag_filters:
                tag_count_subq = (
                    sa.select(sa.func.count(sa.distinct(artifact_tags.c.artifact_id)))
                    .join(Tag, artifact_tags.c.tag_name == Tag.name)
                    .where(
                        sa.and_(
                            artifact_tags.c.artifact_id == Artifact.id,
                            Artifact.project_id.in_(project_ids),
                            sa.func.lower(Tag.name).like(f"%{tag_filters[0].lower()}%")
                        )
                    )
                )
                count_subq = tag_count_subq
            elif id_filters:
                try:
                    artifact_id = int(id_filters[0])
                    id_count_subq = sa.select(sa.func.count(Artifact.id)).where(
                        sa.and_(
                            Artifact.project_id.in_(project_ids),
                            Artifact.id == artifact_id
                        )
                    )
                    count_subq = id_count_subq
                except ValueError:
                    count_subq = sa.select(sa.func.count(Artifact.id)).where(Artifact.id.is_(None))
            elif title_filter:
                title_count_subq = sa.select(sa.func.count(Artifact.id)).where(
                    sa.and_(
                        Artifact.project_id.in_(project_ids),
                        sa.func.lower(Artifact.title).like(f"%{title_filter.lower()}%")
                    )
                )
                count_subq = title_count_subq
        
        count_result = await st.execute(count_subq)
        total_count = count_result.scalar_one_or_none() or 0
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        
        footer_msg_id = None
        if total_pages > 1:
            footer_builder = InlineKeyboardBuilder()
            if page > 1:
                footer_builder.button(text="⬅️ Назад", callback_data=f"aw:page:{page-1}")
            footer_builder.button(text=f"Стр. {page}/{total_pages}", callback_data="aw:noop")
            if page < total_pages:
                footer_builder.button(text="Далее ➡️", callback_data=f"aw:page:{page+1}")
            footer_builder.adjust(3)
            
            footer_msg = await m.bot.send_message(
                chat_id=m.chat.id,
                text="Пагинация:",
                reply_markup=footer_builder.as_markup()
            )
            # Store footer message ID
            footer_msg_id = footer_msg.message_id
        else:
            stt.ask_footer_msg_id = None
        
        # Update state with new message IDs
        stt.ask_page_msg_ids = ",".join(sent_msg_ids) if sent_msg_ids else None
        stt.ask_footer_msg_id = footer_msg_id
        
        await st.commit()
    
    # Always send reply keyboard to prevent it from disappearing
    chat_on, *_ = await get_chat_flags(st, actual_user_id)
    # Removed temporary "..." message - using toast instead
    # await m.answer("...", reply_markup=main_reply_kb(chat_on))

@router.message(F.text == "❓ ASK‑WIZARD")
async def ask_open(message: Message):
    """Open ASK wizard root. This never calls LLM."""
    if not message.from_user:
        return
    async with session_scope() as st:
        stt = await _ensure_user_state(st, message.from_user.id)
        stt.ask_armed = False
        await st.flush()
        
        # Get active project for display
        active_proj = await get_active_project(st, message.from_user.id)
        proj_name = active_proj.name if active_proj else "Нет проекта"
        
        # Get linked projects for display
        linked_ids = await get_linked_project_ids(st, message.from_user.id)
        linked_status = "Linked: ON" if linked_ids else "Linked: OFF"
        
        # Get selected artifacts count and budget
        selected_ids = _ids_get(stt)
        selected_count = len(selected_ids)
        budget_label = await _calc_budget_label(st, selected_ids)
        
        # Build home panel text
        panel_text = f"ASK‑WIZARD (проект: {proj_name})\n"
        if linked_ids:
            panel_text += f"🔒 {linked_status}\n"
        
        # Build inline keyboard for home panel
        b = InlineKeyboardBuilder()
        
        # Search button (always available) - changed to just "🔍" to match specification
        b.button(text="📋 List", callback_data="aw:list")
        
        # Ask button (only when sources selected)
        if selected_count > 0:
            b.button(text="❓ Ask", callback_data="aw:arm")
        b.adjust(2)
        
        # Auto-clear toggle
        b.button(text=f"Auto-clear: {'ON' if stt.auto_clear_selection else 'OFF'}", callback_data="aw:autoclear")
        
        # Reset button
        b.button(text="❌ Сброс", callback_data="aw:clear")
        b.adjust(2, 2)
        
        # Import last button
        b.button(text="📥 Import last", callback_data="aw:import_last")
        b.adjust(2, 2, 1)
        
        # Budget display (non-interactive)
        b.button(text=budget_label or "Бюджет: ~0 токенов", callback_data="aw:noop")
        b.adjust(2, 2, 1, 1)
        
        await message.answer(
            panel_text,
            reply_markup=b.as_markup(),
        )
        
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, message.from_user.id)
        # Removed temporary "..." message - using proper keyboard only

@router.callback_query(F.data == "aw:search")
async def ask_search(cb: CallbackQuery):
    if not cb.from_user:
        # Always include reply keyboard
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            # Removed temporary "..." message
            # if cb.message:
            #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid user")
    await cb.answer()
    if cb.message:
        prompt_msg = await cb.message.answer("Введи название, #тег или id:...", reply_markup=ForceReply(selective=True))
        
        # Set the awaiting_ask_search flag and store prompt message ID
        async with session_scope() as st:
            stt = await _ensure_user_state(st, cb.from_user.id)
            stt.awaiting_ask_search = True
            # Store the prompt message ID for later cleanup
            # Note: We'll need to store this in a different way since UserState doesn't have ask_prompt_msg_id
            # For now, we'll handle cleanup in the reply handler
            await st.commit()
        
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))

@router.message(F.reply_to_message & (F.reply_to_message.text == "Введи название, #тег или id:..."))
async def ask_search_reply(message: Message):
    if not message.from_user or not message.text:
        return
    q = message.text.strip()
    async with session_scope() as st:
        # Reset the awaiting_ask_search flag
        stt = await _ensure_user_state(st, message.from_user.id)
        stt.awaiting_ask_search = False
        
        # Log search parameters for debugging (Hotfix A)
        active_project = await get_active_project(st, message.from_user.id)
        active_project_id = active_project.id if active_project else None
        linked_project_ids = await get_linked_project_ids(st, message.from_user.id)
        
        # Parse the search query to determine mode
        if q.isdigit():
            mode = "id"
        elif q.startswith('#') and len(q) > 1:
            mode = "tag"
        else:
            mode = "name"
        
        print(f"DEBUG: ask_search_reply - mode={mode}, term='{q}', active_project_id={active_project_id}, linked_project_ids={linked_project_ids}, user_id={message.from_user.id}")
        
        await st.commit()
        
        await _render_panel(message, st, q=q, page=1, user_id=message.from_user.id if message.from_user else None)
        
        # Always include reply keyboard
        chat_on, *_ = await get_chat_flags(st, message.from_user.id)
        # Removed temporary "..." message
        # await message.answer("...", reply_markup=main_reply_kb(chat_on))
        
        # Delete the prompt message and user's reply message
        if message.reply_to_message and message.bot:
            await _safe_delete(message.bot, message.chat.id, message.reply_to_message.message_id)
        if message.message_id and message.bot:
            await _safe_delete(message.bot, message.chat.id, message.message_id)

@router.callback_query(F.data == "aw:list")
async def ask_open_list(cb: CallbackQuery):
    """Open the ASK list view (page 1) from the home panel. No LLM involved."""
    if not cb.from_user:
        # Always include reply keyboard
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
            # Removed temporary "..." message
            # if cb.message:
            #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid user")
    async with session_scope() as st:
        # Debug: Check if we can get the active project
        active_proj = await get_active_project(st, cb.from_user.id)
        print(f"DEBUG: active_proj: {active_proj}")
        if active_proj:
            print(f"DEBUG: active_proj.id: {active_proj.id}, active_proj.name: {active_proj.name}")
        
        if cb.message and isinstance(cb.message, Message):
            await _render_panel(cb.message, st, q=None, page=1, user_id=cb.from_user.id)
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        # if cb.message and isinstance(cb.message, Message):
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

@router.callback_query(F.data.startswith("aw:page:"))
async def ask_page(cb: CallbackQuery):
    if not cb.data:
        page = 1
    else:
        try:
            page = int(cb.data.split(":", 2)[-1])
        except Exception:
            page = 1
    async with session_scope() as st:
        if cb.message and isinstance(cb.message, Message):
            await _render_panel(cb.message, st, q=None, page=page, user_id=cb.from_user.id if cb.from_user else None)
            
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        # if cb.message and isinstance(cb.message, Message):
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

@router.callback_query(F.data.startswith("aw:toggle:"))
async def ask_toggle(cb: CallbackQuery):
    if not (cb.from_user and cb.data):
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid")
    try:
        art_id = int(cb.data.split(":", 2)[-1])
    except Exception:
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Bad id")
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id)
        current = set(_ids_get(stt))
        if art_id in current:
            current.remove(art_id)
            action_text = "Убран из выбора"
            new_icon = "➕"
        else:
            current.add(art_id)
            action_text = "Добавлен в выбор"
            new_icon = "✅"  # Changed from "🧺" to "✅" to match specification
        _ids_set(stt, current)
        await st.commit()
        
        # Update the inline keyboard of the current message to show the new icon (instant toggle) (Hotfix E)
        if cb.message and isinstance(cb.message, Message) and cb.message.bot:
            # Create updated inline keyboard with new icon
            builder = InlineKeyboardBuilder()
            builder.button(text=new_icon, callback_data=f"aw:toggle:{art_id}")
            builder.button(text="🗑", callback_data=f"aw:delete:{art_id}")
            builder.adjust(2)
            
            try:
                await cb.message.bot.edit_message_reply_markup(
                    chat_id=cb.message.chat.id,
                    message_id=cb.message.message_id,
                    reply_markup=builder.as_markup()
                )
                # Answer callback without showing alert for instant toggle (Hotfix E)
                await cb.answer(action_text, show_alert=False)
            except Exception as e:
                # Log error but still answer the callback
                print(f"DEBUG: Error updating inline keyboard: {e}")
                await cb.answer(action_text, show_alert=True)
        
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        # if cb.message and isinstance(cb.message, Message):
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    # Callback already answered above, no need to answer again

@router.callback_query(F.data.startswith("aw:delete:"))
async def ask_delete(cb: CallbackQuery):
    if not (cb.from_user and cb.data):
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid")
    try:
        art_id = int(cb.data.split(":", 2)[-1])
    except Exception:
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Bad id")
    
    async with session_scope() as st:
        # Delete the artifact
        artifact = await st.get(Artifact, art_id)
        if artifact:
            await st.delete(artifact)
            await st.commit()
            # Show toast message instead of creating new message
            await _toast(cb, "Артефакт удален")
        else:
            await _toast(cb, "Артефакт не найден")
        
        # Rerender current page
        if cb.message and isinstance(cb.message, Message):
            await _render_panel(cb.message, st, q=None, page=1, user_id=cb.from_user.id if cb.from_user else None)
            
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        # if cb.message and isinstance(cb.message, Message):
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    # Callback already answered above

@router.callback_query(F.data == "aw:autoclear")
async def ask_autoclear(cb: CallbackQuery):
    if not cb.from_user:
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid user")
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id)
        stt.auto_clear_selection = not bool(stt.auto_clear_selection)
        await st.commit()
        budget_label = await _calc_budget_label(st, _ids_get(stt))
        if cb.message:
            await cb.message.answer(
                "ASK панель:",
                reply_markup=_panel_kb(len(_ids_get(stt)), budget_label, stt.auto_clear_selection),
            )
            
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        # if cb.message:
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

@router.callback_query(F.data == "aw:clear")
async def ask_clear(cb: CallbackQuery):
    if not cb.from_user:
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid user")
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id)
        _ids_set(stt, [])
        stt.ask_armed = False
        await st.commit()
        if cb.message:
            await cb.message.answer(
                "ASK панель:",
                reply_markup=_panel_kb(0, "Бюджет: ~0 токенов", stt.auto_clear_selection),
            )
            
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        # if cb.message:
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer("Очищено")

@router.callback_query(F.data == "aw:arm")
async def ask_arm(cb: CallbackQuery):
    if not cb.from_user:
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid user")
    async with session_scope() as st:
        stt = await _ensure_user_state(st, cb.from_user.id)
        sel = _ids_get(stt)
        if not sel:
            # Always include reply keyboard
            # Removed temporary "..." message
            # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
            # if cb.message:
            #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
            return await cb.answer("Не выбрано ни одного источника", show_alert=True)
        stt.ask_armed = True
        await st.commit()
        chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        if cb.message:
            if not chat_on:
                # Create inline button to toggle chat
                inline_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Включить чат", callback_data="aw:toggle_chat")]
                ])
                await cb.message.answer(
                    "Включи чат (кнопка внизу), затем отправь вопрос", 
                    reply_markup=inline_kb
                )
                # Also send the reply keyboard to prevent it from disappearing
                # Removed temporary "..." message
                # await cb.message.answer("...", reply_markup=main_reply_kb(False))
            else:
                await cb.message.answer("Введите вопрос…", reply_markup=ForceReply(selective=True))
                
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        # if cb.message:
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

@router.callback_query(F.data == "aw:toggle_chat")
async def ask_toggle_chat(cb: CallbackQuery):
    """Inline button handler to toggle chat on."""
    if not cb.from_user:
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid user")
    async with session_scope() as st:
        from app.services.memory import set_chat_mode
        await set_chat_mode(st, cb.from_user.id, on=True)
        await st.commit()
        # Re-render the ASK panel with chat now enabled
        stt = await _ensure_user_state(st, cb.from_user.id)
        sel = _ids_get(stt)
        budget_label = await _calc_budget_label(st, sel)
        if cb.message:
            await cb.message.answer(
                "ASK панель:",
                reply_markup=_panel_kb(len(sel), budget_label, stt.auto_clear_selection),
            )
            
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        # if cb.message:
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer("Чат включен")

@router.callback_query(F.data == "aw:import_last")
async def ask_import_last(cb: CallbackQuery):
    """Handler for importing the last document from ASK wizard."""
    # Import the last document by calling the wizard_import_last function directly
    from app.handlers.menu import wizard_import_last
    return await wizard_import_last(cb)

@router.callback_query(F.data == "aw:clear_search")
async def ask_clear_search(cb: CallbackQuery):
    """Clear search filter and re-render panel."""
    if not cb.from_user:
        # Always include reply keyboard
        # Removed temporary "..." message
        # async with session_scope() as st:
        #     chat_on, *_ = await get_chat_flags(st, cb.from_user.id if cb.from_user else 0)
        #     if cb.message:
        #         await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
        return await cb.answer("Invalid user")
    
    async with session_scope() as st:
        if cb.message and isinstance(cb.message, Message):
            await _render_panel(cb.message, st, q=None, page=1)
            
        # Always include reply keyboard
        # Removed temporary "..." message
        # chat_on, *_ = await get_chat_flags(st, cb.from_user.id)
        # if cb.message and isinstance(cb.message, Message):
        #     await cb.message.answer("...", reply_markup=main_reply_kb(chat_on))
    await cb.answer()

# Public entry used by chat free-text handler. LLM calls are DISABLED here on purpose.
async def run_question_with_selection(message: Message, prompt: str):
    if not message.from_user:
        return
    async with session_scope() as st:
        stt = await _ensure_user_state(st, message.from_user.id)
        sel = _ids_get(stt)
        # Compose a debug-only echo with selected ids. Do not call any LLM here.
        if not sel:
            # Always include reply keyboard to prevent it from disappearing
            chat_on, *_ = await get_chat_flags(st, message.from_user.id)
            await message.answer("ASK: источники не выбраны (LLM отключён, тест-режим).", reply_markup=main_reply_kb(chat_on))
            return
        # Escape prompt in simple way (no HTML parse here to avoid injection)
        try:
            from html import escape as _esc
            safe_prompt = _esc(prompt)
        except Exception:
            safe_prompt = prompt
        response_text = (
            "🧪 TEST: LLM отключён.\n"
            f"Вопрос: {safe_prompt}\n"
            f"Источники: {', '.join(map(str, sel))}"
        )
        await message.answer(response_text)
        
        # Reset armed and optionally clear selection
        if stt.auto_clear_selection:
            _ids_set(stt, [])
            # Re-render panel if auto-clear is on
            budget_label = await _calc_budget_label(st, [])
            controls = _panel_kb(0, budget_label, stt.auto_clear_selection)
            
            # Edit existing panel or send new one
            if stt.last_panel_msg_id and message.chat and message.bot:
                try:
                    await message.bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=stt.last_panel_msg_id,
                        text="ASK панель:",
                        reply_markup=controls
                    )
                except Exception:
                    # If editing fails, send a new message
                    await message.answer("ASK панель:", reply_markup=controls)
            else:
                await message.answer("ASK панель:", reply_markup=controls)
        
        stt.ask_armed = False
        await st.commit()
        
        # Always include reply keyboard to prevent it from disappearing
        chat_on, *_ = await get_chat_flags(st, message.from_user.id)
        await message.answer("...", reply_markup=main_reply_kb(chat_on))
