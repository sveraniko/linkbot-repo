# app/handlers/import_file.py
from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
import re
import datetime as dt

from app.config import settings
from app.services.memory import get_active_project
from app.services.artifacts import create_import
from app.storage import save_file
from app.db import session_scope
from app.ignore import load_pmignore, iter_text_files

router = Router()

# Храним "последний документ" на пользователя/чат (fallback, если нет reply)
# ключ: (chat_id, user_id) -> (file_id, file_name)
_LAST_DOC: dict[tuple[int, int], tuple[str, str]] = {}

ALLOWED_EXTS = {".txt", ".md", ".json"}

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
    doc = message.document
    if not doc or not message.from_user:
        return
    _LAST_DOC[(message.chat.id, message.from_user.id)] = (doc.file_id, doc.file_name or "file")
    await message.answer("Получил документ. Теперь ответьте на него командой /import, чтобы импортировать его в память проекта.")

@router.message(Command("import"))
async def import_document(message: Message):
    if not message.from_user:
        return
        
    async with session_scope() as st:
        proj = await get_active_project(st, message.from_user.id)
        if not proj:
            await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
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
                    await message.answer("Ошибка доступа к боту")
                    return
                tg_file = await message.bot.get_file(file_id)
                if not tg_file.file_path:
                    await message.answer("Не удалось получить путь к файлу")
                    return
                file_bytes_io = await message.bot.download_file(tg_file.file_path)
                if not file_bytes_io:
                    await message.answer("Не удалось скачать файл")
                    return
                data = file_bytes_io.read()
                ext = Path(file_name).suffix.lower()
                if ext not in ALLOWED_EXTS:
                    await message.answer("Файл найден, но расширение не поддерживается. Доступно: .txt .md .json")
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
                await message.answer(
                    f"Импортировано в <b>{proj.name}</b>: {title}\nURI: {uri or '—'}\nТеги: {', '.join(tags) if tags else '—'}"
                )
                return

            # Вообще ничего не нашли
            await message.answer("Пришлите файл .txt/.md/.json и ответьте на него командой /import для импорта.")
            return

        # 3) Ветка с reply (основной happy-path)
        file_name = doc.file_name or "import.txt"
        ext = Path(file_name).suffix.lower()
        if ext not in ALLOWED_EXTS:
            await message.answer("Файл получен, но расширение не поддерживается. Доступно: .txt .md .json")
            return

        if not message.bot:
            await message.answer("Ошибка доступа к боту")
            return
            
        tg_file = await message.bot.get_file(doc.file_id)
        if not tg_file.file_path:
            await message.answer("Не удалось получить путь к файлу")
            return
            
        file_bytes_io = await message.bot.download_file(tg_file.file_path)
        if not file_bytes_io:
            await message.answer("Не удалось скачать файл")
            return
            
        data = file_bytes_io.read()
        uri = await save_file(file_name, data)  # MinIO (публичный URL или None)
        text = data.decode("utf-8", errors="ignore")
        tags = _parse_tags(message.text)
        # Add auto-tag date
        date_tag = f"rel-{dt.datetime.utcnow().date().isoformat()}"
        if tags:
            tags.append(date_tag)
        else:
            tags = [date_tag]

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
        await message.answer(
            f"Импортировано в <b>{proj.name}</b>: {file_name}\nURI: {uri or '—'}\nТеги: {', '.join(tags) if tags else '—'}"
        )

# --- ZIP import ---
@router.message(Command("importzip"))
async def import_zip(message: Message):
    # Проверяем, что есть reply с документом
    if not message.reply_to_message or not message.reply_to_message.document:
        return await message.answer("Прикрепите ZIP как файл и ответьте на сообщение с командой:\n<code>/importzip tags code,snapshot,rev-YYYY-MM-DD</code>")
    
    doc = message.reply_to_message.document
    if not doc.file_name or not doc.file_name.endswith(".zip"):
        return await message.answer("Файл должен быть .zip")
    
    # Получаем теги из команды
    tags = _parse_tags(message.text)
    # Add auto-tag date
    date_tag = f"rel-{dt.datetime.utcnow().date().isoformat()}"
    if tags:
        tags.append(date_tag)
    else:
        tags = [date_tag]
    
    # Скачиваем ZIP
    if not message.bot:
        return await message.answer("Ошибка доступа к боту")
        
    tg_file = await message.bot.get_file(doc.file_id)
    if not tg_file.file_path:
        return await message.answer("Не удалось получить путь к файлу")
        
    file_bytes_io = await message.bot.download_file(tg_file.file_path)
    if not file_bytes_io:
        return await message.answer("Не удалось скачать файл")
        
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
            title = f"{doc.file_name}:{rel}"
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
    
    await message.answer(f"Импорт ZIP завершён: {imported} файлов.\nТег: <code>{date_tag}</code>")

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
        await message.answer("Файл найден, но расширение не поддерживается. Доступно: .txt .md .json")
        return True
    uri = await save_file(file_name, data)
    text = data.decode("utf-8", errors="ignore")
    proj = await get_active_project(st, message.from_user.id)
    if not proj:
        await message.answer("Сначала выберите проект: <code>/project &lt;name&gt;</code>")
        return True
    # Add auto-tag date
    date_tag = f"rel-{dt.datetime.utcnow().date().isoformat()}"
    if tags:
        tags.append(date_tag)
    else:
        tags = [date_tag]
    await create_import(
        st, proj,
        title=file_name, text=text,
        chunk_size=settings.chunk_size, overlap=settings.chunk_overlap,
        tags=tags, uri=uri,
    )
    await st.commit()
    await message.answer(f"Импортировано в <b>{proj.name}</b>: {file_name}\nURI: {uri or '—'}\nТеги: {', '.join(tags) if tags else '—'}")
    return True