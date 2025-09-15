from __future__ import annotations
import re

CYR_RX = re.compile(r'[\u0400-\u04FF]')  # диапазон кириллицы

def fix_zip_name(name: str, flag_bits: int) -> str:
    """
    Если ZIP-файл НЕ пометил имя как UTF-8 (flag bit 11),
    zipfile декодирует его как CP437. Пытаемся восстановить.
    """
    # 0x800 == UTF-8 filename flag (bit 11)
    if flag_bits & 0x800:
        return name  # уже ок

    # пробуем "откатить" в байты CP437 и заново декодировать распространённые кодировки RU
    try:
        raw = name.encode("cp437", errors="strict")
    except Exception:
        # запасной вариант: latin1 даёт прямой маппинг 1:1 байт<->символ
        raw = name.encode("latin1", errors="replace")

    for enc in ("cp866", "cp1251", "koi8_r"):
        try:
            s = raw.decode(enc)
            if CYR_RX.search(s):
                return s
        except Exception:
            pass

    return name  # не угадали — оставим как есть

def decode_text_bytes(data: bytes) -> str:
    """
    Корректная декодировка содержимого: сначала UTF-8, затем популярные RU-энкодинги.
    """
    try:
        return data.decode("utf-8")
    except Exception:
        pass
    for enc in ("cp1251", "cp866", "koi8_r", "utf-16"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    # последний шанс: "сломанный" UTF-8 без падения
    return data.decode("utf-8", errors="ignore")