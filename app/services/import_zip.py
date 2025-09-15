from __future__ import annotations
import zipfile, io, re, secrets
from app.services.artifacts import create_import
from app.ignore import should_ignore
from app.utils.zipfix import fix_zip_name, decode_text_bytes   # у тебя уже есть
from zoneinfo import ZoneInfo
from datetime import datetime
from typing import List, Tuple, Optional

BERLIN = ZoneInfo("Europe/Berlin")

def _rand_batch():
    # 4 символа [a-z0-9]
    return ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(4))

_slug_rx = re.compile(r'[^a-z0-9\-]+')

def _name_tag_from_basename(name: str) -> str | None:
    base = name.split("/")[-1].rsplit(".", 1)[0].lower().replace(" ", "-")
    base = _slug_rx.sub('-', base).strip('-')
    return f"name:{base}" if base else None

_chat_rx = re.compile(r'\[([^\]]{6,64})\]')

def _chat_tag_from_name(name: str) -> str | None:
    m = _chat_rx.search(name)
    return f"chat:{m.group(1)}" if m else None

async def import_zip_bytes(session, project, data: bytes, base_name: str,
                           extra_tags: list[str] | None = None,
                           chunk_size: int = 1600, overlap: int = 150) -> tuple[list[int], str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    created_ids: list[int] = []
    date_tag = f"rel-{datetime.now(BERLIN).date().isoformat()}"
    batch = _rand_batch()
    batch_tag = f"batch-{batch}"
    for info in z.infolist():
        if info.is_dir():
            continue
        fixed_name = fix_zip_name(info.filename, info.flag_bits)
        if should_ignore(fixed_name):
            continue
        if not fixed_name.lower().endswith((".md",".txt",".json")):
            continue
        raw = z.read(info)
        text = decode_text_bytes(raw)
        # авто-теги этого файла
        per_file = [date_tag, batch_tag]
        nt = _name_tag_from_basename(fixed_name)
        if nt: per_file.append(nt)
        ct = _chat_tag_from_name(fixed_name)
        if ct: per_file.append(ct)
        if extra_tags:
            per_file.extend(extra_tags)
        art = await create_import(session, project, title=fixed_name, text=text,
                                  chunk_size=chunk_size, overlap=overlap, tags=per_file)
        await session.flush()
        created_ids.append(art.id)
    return created_ids, batch_tag