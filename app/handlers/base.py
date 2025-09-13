from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.artifacts import get_or_create_project, create_note
from app.services.memory import (
    set_active_project, get_active_project, list_artifacts, clear_project,
    set_context_filters, set_preferred_model, get_preferred_model
)
from app.config import settings
from app.db import get_session
from app.states import state_manager
import logging

logger = logging.getLogger(__name__)
router = Router()
_pending_clear: dict[int, str] = {}

@router.message(Command("start"))
async def start(message: Message):
    from app.db import session_scope
    from app.services.memory import get_chat_flags
    from app.handlers.keyboard import build_kb_minimal
    async with session_scope() as st:
        if message.from_user:
            chat_on, _, _, _ = await get_chat_flags(st, message.from_user.id)
            await message.answer(
                "Привет!\n"
                "<code>/project &lt;name&gt;</code> — выбрать/создать проект\n"
                "<code>/memory add &lt;text&gt;</code> — добавить заметку\n"
                "<code>/memory show</code> — показать краткий конспект\n"
                "<code>/memory list</code> — список артефактов\n"
                "<code>/import</code> — ответьте этой командой на .txt/.md/.json файл\n"
                "<code>/ask &lt;вопрос&gt;</code> — спросить с учётом памяти\n"
                "<code>/ctx kinds note,import</code> | <code>/ctx tags api,db</code> — фильтры контекста\n"
                "<code>/memory clear</code> — безопасная очистка (двухшаговая)\n"
                "<code>/model gpt-5</code> | <code>/model gpt-5-thinking</code> — выбрать модель\n"
                "\nОткрой меню: <code>/menu</code>",
                reply_markup=build_kb_minimal(chat_on)
            )

@router.message(Command("ctx"))
async def ctx_filters(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    args = (message.text.split(maxsplit=1) + [""])[1]
    if not args:
        await message.answer("Пример: /ctx kinds note,import | /ctx tags api,db | /ctx reset")
        return
    if args.startswith("kinds"):
        kinds_csv = args.replace("kinds", "", 1).strip()
        await set_context_filters(st, message.from_user.id, kinds_csv=kinds_csv)
    elif args.startswith("tags"):
        tags_csv = args.replace("tags", "", 1).strip()
        await set_context_filters(st, message.from_user.id, tags_csv=tags_csv)
    elif args.strip() == "reset":
        await set_context_filters(st, message.from_user.id, kinds_csv="", tags_csv="")
    await st.commit()
    await message.answer("Ок. Фильтры обновлены.")

@router.message(Command("model"))
async def set_model(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        current = await get_preferred_model(st, message.from_user.id)
        await message.answer(f"Текущая модель: <b>{current}</b>\nДоступно: gpt-5, gpt-5-thinking\nПример: /model gpt-5-thinking")
        return
    chosen = parts[1].strip()
    applied = await set_preferred_model(st, message.from_user.id, chosen)
    await st.commit()
    await message.answer(f"Ок. Модель установлена: <b>{applied}</b>")

@router.message(Command("project"))
async def project_select(message: Message, session: AsyncSession = get_session()):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажите имя проекта: <code>/project &lt;name&gt;</code>")
        return
    name = parts[1].strip()
    st = await anext(session)  # получаем ОДНУ сессию и переиспользуем
    proj = await get_or_create_project(st, name)
    await set_active_project(st, message.from_user.id, proj)
    await st.commit()
    await message.answer(f"Активен проект: <b>{proj.name}</b>")

@router.message(Command("memory"), F.text.regexp(r"^/memory\s+add\b"))
async def memory_add(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    from app.services.memory import get_active_project
    proj = await get_active_project(st, message.from_user.id)
    if not proj:
        await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return
    
    # Парсим текст с возможными тегами: /memory add #tag1 #tag2 текст
    text_part = message.text.split("add", 1)[1].strip()
    if not text_part:
        await message.answer("Добавьте текст: <code>/memory add &lt;text&gt;</code>\nМожно добавить теги: <code>/memory add #тег1 #тег2 текст</code>")
        return
    
    # Извлекаем теги и текст
    words = text_part.split()
    tags = []
    text_words = []
    
    for word in words:
        if word.startswith('#') and len(word) > 1:
            tags.append(word[1:])  # Убираем #
        else:
            text_words.append(word)
    
    text = ' '.join(text_words)
    if not text:
        await message.answer("Текст заметки не может быть пустым")
        return
    
    # Автоматически добавляем тег 'note' для всех заметок
    if 'note' not in tags:
        tags.append('note')
    
    try:
        await create_note(
            st, proj, 
            title=text[:60], 
            text=text, 
            chunk_size=settings.chunk_size, 
            overlap=settings.chunk_overlap,
            tags=tags if tags else None
        )
        await st.commit()
        
        tags_str = ', '.join(tags) if tags else 'нет'
        await message.answer(
            f"✅ Заметка добавлена в память проекта.\n"
            f"🏷️ Теги: {tags_str}"
        )
    except Exception as e:
        logger.error(f"Error adding note: {e}")
        await message.answer("⚠️ Ошибка при добавлении заметки")

@router.message(Command("memory"), F.text.regexp(r"^/memory\s+list($|\s)"))
async def memory_list(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    from app.services.memory import get_active_project
    proj = await get_active_project(st, message.from_user.id)
    if not proj:
        await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return
    
    arts = await list_artifacts(st, proj)
    if not arts:
        await message.answer("💭 Память пуста.")
        return
    
    # Парсим фильтры из команды: /memory list tag=code kind=import
    command_text = message.text.strip()
    filters = {}
    
    # Простой парсинг фильтров
    if 'tag=' in command_text:
        tag_filter = command_text.split('tag=')[1].split()[0]
        filters['tag'] = tag_filter
    if 'kind=' in command_text:
        kind_filter = command_text.split('kind=')[1].split()[0]
        filters['kind'] = kind_filter
    
    # Применяем фильтры
    filtered_arts = []
    for art in arts:
        include = True
        if 'tag' in filters and filters['tag'] not in (art.tags or []):
            include = False
        if 'kind' in filters and art.kind != filters['kind']:
            include = False
        if include:
            filtered_arts.append(art)
    
    if not filtered_arts:
        filter_desc = ', '.join([f"{k}={v}" for k, v in filters.items()])
        await message.answer(f"🔍 По фильтрам {filter_desc} ничего не найдено.")
        return
    
    lines = [f"📁 Артефакты проекта <b>{proj.name}</b>:"]
    for i, a in enumerate(filtered_arts[:20], 1):  # Показываем только первые 20
        icon = "📝" if a.kind == "note" else "📄"
        tags_str = ", ".join(a.tags) if a.tags else "нет"
        storage_info = f" 🗃️" if a.storage_key else ""
        lines.append(f"{i}. {icon} <b>{a.title}</b>\n   🏷️ {tags_str}{storage_info}")
    
    if len(filtered_arts) > 20:
        lines.append(f"\n... и ещё {len(filtered_arts) - 20} артефактов")
    
    if filters:
        filter_desc = ', '.join([f"{k}={v}" for k, v in filters.items()])
        lines.append(f"\n🔍 Фильтр: {filter_desc}")
    
    await message.answer("\n".join(lines))

@router.message(Command("memory"), F.text.regexp(r"^/memory\s+show$"))
async def memory_show(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    from app.services.memory import get_active_project
    proj = await get_active_project(st, message.from_user.id)
    if not proj:
        await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return
    arts = await list_artifacts(st, proj)
    if not arts:
        await message.answer("Память пуста.")
        return
    preview = []
    for a in arts[:10]:
        preview.append(f"[{a.kind}] <b>{a.title}</b>\n{(a.raw_text[:200] + '...') if len(a.raw_text)>200 else a.raw_text}")
    await message.answer("\n\n".join(preview))

@router.message(Command("memory"), F.text.regexp(r"^/memory\s+clear$"))
async def memory_clear_ask(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    proj = await get_active_project(st, message.from_user.id)
    if not proj:
        await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return
    _pending_clear[message.from_user.id] = proj.name
    await message.answer(f"⚠️ Подтверждение: /memory clear {proj.name}")

@router.message(Command("memory"), F.text.regexp(r"^/memory\s+clear\s+.+$"))
async def memory_clear_confirm(message: Message, session: AsyncSession = get_session()):
    st = await anext(session)
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: <code>/memory clear &lt;project&gt;</code>")
        return
    proj_name = parts[2]
    expected = _pending_clear.get(message.from_user.id)
    if expected != proj_name:
        await message.answer("Имя проекта не совпало или нет ожидания очистки.")
        return
    proj = await get_active_project(st, message.from_user.id)
    if not proj or proj.name != proj_name:
        await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return
    await clear_project(st, proj)
    await st.commit()
    _pending_clear.pop(message.from_user.id, None)
    await message.answer("Память проекта очищена.")