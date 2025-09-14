# app/handlers/import_file.py
from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from pathlib import Path
import re
import datetime as dt
from html import escape

from app.config import settings
from app.services.memory import get_active_project, _ensure_user_state
from app.services.artifacts import create_import
from app.storage import save_file
from app.db import session_scope
from app.ignore import load_pmignore, iter_text_files

router = Router()

# Храним "последний документ" на пользователя/чат (fallback, если нет reply)
# ключ: (chat_id, user_id) -> (file_id, file_name)
_LAST_DOC: dict[tuple[int, int], tuple[str, str]] = {}

ALLOWED_EXTS = {".txt", ".md", ".json", ".zip"}

def _extract_doc_tag(name: str) -> str | None:
    m = re.search(r'(\d{4})(\d{2})(\d{2})', (name or "").lower())
    return f"doc-{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _parse_tags(cmd_text: str | None) -> list[str] | None:
    if not cmd_text:
        return None
    # /import tags a,b,c
    m = re.search(r"/import(?:\s+tags\s+(.*))?$", cmd_text, flags=re.IGNORECASE)
    if not m:
        return None
    grp = m.group(1)
    if not grp:
        return None
    return [t.strip() for t in grp.split(",") if t.strip()]

@router.message(F.document)
async def on_document(message: Message):
    """Ловим любой документ и запоминаем его как 'последний' для юзера в этом чате."""
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    doc = message.document
    if not doc or not message.from_user:
        return
    _LAST_DOC[(message.chat.id, message.from_user.id)] = (doc.file_id, doc.file_name or "file")
    
    # Save to database as well
    async with session_scope() as st:
        stt = await _ensure_user_state(st, message.from_user.id)
        stt.last_doc_file_id = doc.file_id
        stt.last_doc_name = doc.file_name or "file"
        stt.last_doc_mime = doc.mime_type or ""
        await st.commit()
        
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        # For ZIP files, provide special instructions
        if doc.file_name and doc.file_name.lower().endswith(".zip"):
            await message.answer("Получил ZIP-архив. Теперь ответьте на него командой /import, "
                                 "или нажмите в Actions: «Импорт (последний файл)».", reply_markup=build_reply_kb(chat_on))
        else:
            await message.answer("Получил документ. Теперь ответьте на него командой /import, "
                                 "или нажмите в Actions: «Импорт (последний файл)».", reply_markup=build_reply_kb(chat_on))

@router.message(Command("import"))
async def import_document(message: Message):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    if not message.from_user:
        return
        
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id)
        if not proj:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>", reply_markup=build_reply_kb(chat_on))
            return

        # 1) Пытаемся взять документ из reply
        doc = None
        if message.reply_to_message and message.reply_to_message.document:
            doc = message.reply_to_message.document

        # 2) Если нет reply — берем последний документ пользователя в этом чате
        if not doc:
            key = (message.chat.id, message.from_user.id)
            last = _LAST_DOC.get(key)
            if last:
                file_id, file_name = last
                # подтягиваем объект файла через get_file, чтобы скачать
                # В aiogram для скачивания нужен FilePath -> берем через bot.get_file(file_id)
                if not message.bot:
                    # Get chat_on flag to rebuild keyboard with correct state
                    chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
                    await message.answer("Ошибка доступа к боту", reply_markup=build_reply_kb(chat_on))
                    return
                tg_file = await message.bot.get_file(file_id)
                if not tg_file.file_path:
                    # Get chat_on flag to rebuild keyboard with correct state
                    chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
                    await message.answer("Не удалось получить путь к файлу", reply_markup=build_reply_kb(chat_on))
                    return
                file_bytes_io = await message.bot.download_file(tg_file.file_path)
                if not file_bytes_io:
                    # Get chat_on flag to rebuild keyboard with correct state
                    chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
                    await message.answer("Не удалось скачать файл", reply_markup=build_reply_kb(chat_on))
                    return
                data = file_bytes_io.read()
                ext = Path(file_name).suffix.lower()
                if ext not in ALLOWED_EXTS:
                    # Get chat_on flag to rebuild keyboard with correct state
                    chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
                    await message.answer("Файл найден, но расширение не поддерживается. Доступно: .txt .md .json .zip", reply_markup=build_reply_kb(chat_on))
                    return
                uri = await save_file(file_name, data)  # MinIO (может вернуть None, если не настроен)
                text = data.decode("utf-8", errors="ignore")
                title = file_name or "import.txt"
                tags = _parse_tags(message.text)
                # Add auto-tag date
                date_tag = f"rel-{dt.datetime.utcnow().date().isoformat()}"
                if tags:
                    tags.append(date_tag)
                else:
                    tags = [date_tag]
                await create_import(
                    st, proj,
                    title=title,
                    text=text,
                    chunk_size=settings.chunk_size,
                    overlap=settings.chunk_overlap,
                    tags=tags,
                    uri=uri,
                )
                await st.commit()
                # Get chat_on flag to rebuild keyboard with correct state
                chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
                await message.answer(
                    f"Импортировано в <b>{escape(proj.name)}</b>: {escape(title)}\nURI: {uri or '—'}\nТеги: {', '.join(tags) if tags else '—'}",
                    reply_markup=build_reply_kb(chat_on)
                )
                return

            # Вообще ничего не нашли
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            await message.answer("Пришлите файл .txt/.md/.json/.zip и ответьте на него командой /import для импорта.", reply_markup=build_reply_kb(chat_on))
            return

        # 3) Ветка с reply (основной happy-path)
        file_name = doc.file_name or "import.txt"
        ext = Path(file_name).suffix.lower()
        if ext not in ALLOWED_EXTS:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            await message.answer("Файл получен, но расширение не поддерживается. Доступно: .txt .md .json .zip", reply_markup=build_reply_kb(chat_on))
            return

        if not message.bot:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            await message.answer("Ошибка доступа к боту", reply_markup=build_reply_kb(chat_on))
            return
            
        tg_file = await message.bot.get_file(doc.file_id)
        if not tg_file.file_path:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            await message.answer("Не удалось получить путь к файлу", reply_markup=build_reply_kb(chat_on))
            return
            
        file_bytes_io = await message.bot.download_file(tg_file.file_path)
        if not file_bytes_io:
            # Get chat_on flag to rebuild keyboard with correct state
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
            await message.answer("Не удалось скачать файл", reply_markup=build_reply_kb(chat_on))
            return
            
        data = file_bytes_io.read()
        uri = await save_file(file_name, data)  # MinIO (публичный URL или None)
        text = data.decode("utf-8", errors="ignore")
        tags = _parse_tags(message.text)
        # Add auto-tag date
        date_tag = f"rel-{dt.datetime.utcnow().date().isoformat()}"
        doc_tag = _extract_doc_tag(file_name)
        if tags:
            tags.append(date_tag)
            if doc_tag:
                tags.append(doc_tag)
        else:
            tags = [date_tag]
            if doc_tag:
                tags.append(doc_tag)

        await create_import(
            st, proj,
            title=file_name,
            text=text,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            tags=tags,
            uri=uri,
        )
        await st.commit()
        # Get chat_on flag to rebuild keyboard with correct state
        chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        await message.answer(
            f"Импортировано в <b>{escape(proj.name)}</b>: {escape(file_name)}\nURI: {uri or '—'}\nТеги: {', '.join(tags) if tags else '—'}",
            reply_markup=build_reply_kb(chat_on)
        )

# --- ZIP import ---
@router.message(Command("importzip"))
async def import_zip(message: Message):
    from app.handlers.keyboard import build_reply_kb
    from app.services.memory import get_chat_flags
    # Проверяем, что есть reply с документом
    if not message.reply_to_message or not message.reply_to_message.document:
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        return await message.answer("Прикрепите ZIP как файл и ответьте на сообщение с командой:\n<code>/importzip tags code,snapshot,rev-YYYY-MM-DD</code>", reply_markup=build_reply_kb(chat_on))
    
    doc = message.reply_to_message.document
    if not doc.file_name or not doc.file_name.endswith(".zip"):
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        return await message.answer("Файл должен быть быть .zip", reply_markup=build_reply_kb(chat_on))
    
    # Получаем теги из команды
    tags = _parse_tags(message.text)
    # Add auto-tag date
    date_tag = f"rel-{dt.datetime.utcnow().date().isoformat()}"
    doc_tag = _extract_doc_tag(doc.file_name or "")
    if tags:
        tags.append(date_tag)
        if doc_tag:
            tags.append(doc_tag)
    else:
        tags = [date_tag]
        if doc_tag:
            tags.append(doc_tag)
    
    # Скачиваем ZIP
    if not message.bot:
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        return await message.answer("Ошибка доступа к боту", reply_markup=build_reply_kb(chat_on))
        
    tg_file = await message.bot.get_file(doc.file_id)
    if not tg_file.file_path:
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        return await message.answer("Не удалось получить путь к файлу", reply_markup=build_reply_kb(chat_on))
        
    file_bytes_io = await message.bot.download_file(tg_file.file_path)
    if not file_bytes_io:
        # Get chat_on flag to rebuild keyboard with correct state
        async with session_scope() as st:
            chat_on, *_ = await get_chat_flags(st, message.from_user.id if message.from_user else 0)
        return await message.answer("Не удалось скачать файл", reply_markup=build_reply_kb(chat_on))
        
    data = file_bytes_io.read()
    
    # Сохраняем временно
    tmp = Path("/tmp/pm_zip")
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp)

    tmp.mkdir(parents=True, exist_ok=True)
    
    # Сохраняем ZIP
    zip_path = tmp / doc.file_name
    with open(zip_path, "wb") as f:
        f.write(data)
    
    # Распаковываем
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
    
    # Загружаем .pmignore
    spec = load_pmignore(tmp)
    
    # Импортируем текстовые файлы
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id if message.from_user else 0)
        if not proj:
            return await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        
        imported = 0
        for rel, text in iter_text_files(tmp, spec):
            title = f"{escape(doc.file_name)}:{rel}"
            await create_import(
                st, proj,
                title=title,
                text=text,
                chunk_size=settings.chunk_size,
                overlap=settings.chunk_overlap,
                tags=tags
            )
            imported += 1
        await st.commit()
    
    await message.answer(f"Импорт ZIP завершён: {imported} файлов.\nТег: <code>{escape(date_tag)}</code>")

# --- экспортируем для меню ---
async def import_last_for_user(message: Message, st: AsyncSession, tags: list[str] | None) -> bool:
    if not message.from_user:
        return False
    key = (message.chat.id, message.from_user.id)
    last = _LAST_DOC.get(key)
    if not last:
        return False
    file_id, file_name = last
    if not message.bot:
        await message.answer("Ошибка доступа к боту")
        return True
    tg_file = await message.bot.get_file(file_id)
    if not tg_file.file_path:
        await message.answer("Не удалось получить путь к файлу")
        return True
    file_bytes_io = await message.bot.download_file(tg_file.file_path)
    if not file_bytes_io:
        await message.answer("Не удалось скачать файл")
        return True
    data = file_bytes_io.read()
    ext = Path(file_name).suffix.lower()
    if ext not in ALLOWED_EXTS:
        await message.answer("Файл найден, но расширение не поддерживается. Доступно: .txt .md .json .zip")
        return True
        
    proj = await get_active_project(st, message.from_user.id)
    if not proj:
        await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return True
        
    # Add auto-tag date
    date_tag = f"rel-{dt.datetime.utcnow().date().isoformat()}"
    doc_tag = _extract_doc_tag(file_name)
    if tags:
        tags.append(date_tag)
        if doc_tag:
            tags.append(doc_tag)
    else:
        tags = [date_tag]
        if doc_tag:
            tags.append(doc_tag)
        
    if ext == ".zip":
        # Handle ZIP file import
        import zipfile
        import tempfile
        from app.ignore import load_pmignore, iter_text_files
        
        try:
            # Create temporary directory and file
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                zip_path = tmp_path / "temp.zip"
                
                # Write ZIP data to temporary file
                with open(zip_path, "wb") as f:
                    f.write(data)
                
                # Extract ZIP
                with zipfile.ZipFile(zip_path) as z:
                    z.extractall(tmp_path)
                
                # Load .pmignore spec
                spec = load_pmignore(tmp_path)
                
                # Import text files
                imported_count = 0
                for rel_path, content in iter_text_files(tmp_path, spec):
                    title = f"{escape(file_name)}:{escape(rel_path)}"
                    await create_import(
                        st, proj,
                        title=title,
                        text=content,
                        chunk_size=settings.chunk_size,
                        overlap=settings.chunk_overlap,
                        tags=tags + ['zip']
                    )
                    imported_count += 1
                
                await st.commit()
                await message.answer(f"Импортировано из ZIP в <b>{escape(proj.name)}</b>: {imported_count} файлов\nАрхив: {escape(file_name)}\nТеги: {', '.join(tags) if tags else '—'}")
                return True
                
        except Exception as e:
            await message.answer(f"Ошибка при импорте ZIP: {escape(str(e))}")
            return True
    else:
        # Handle regular text files
        uri = await save_file(file_name, data)
        text = data.decode("utf-8", errors="ignore")
        await create_import(
            st, proj,
            title=file_name, text=text,
            chunk_size=settings.chunk_size, overlap=settings.chunk_overlap,
            tags=tags, uri=uri,
        )
        await st.commit()
        await message.answer(f"Импортировано в <b>{escape(proj.name)}</b>: {escape(file_name)}\nURI: {uri or '—'}\nТеги: {', '.join(tags) if tags else '—'}")
        return True
