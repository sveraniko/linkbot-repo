# ASK‑WIZARD v3 — Sprint Breakdown (Memory внутри ASK, единый поток)

> Цель спринта: довести проект до **единого мастера**: Memory встроен в ASK, поиск живёт на List, «плашки + одна строка кнопок», **мгновенный** `➕→✅`, **5/страница**, **LLM запрещён**, Linked‑правила и Auto‑clear.  
> Выполнять по шагам. Каждый шаг — маленький PR. В конце чек‑лист приёмки.

---

## 0) Подготовка (одноразово)

**Где:** `app/handlers/ask.py`, `app/handlers/memory_panel.py`, `app/services/memory.py`, `app/keyboards/ui.py` (или ваш модуль констант).

- Создать UI‑константы (иконки/тексты): `PLUS="➕"`, `SELECTED="✅"`, `DEL="🗑"`, `PREV="⬅️"`, `NEXT="➡️"`.
- В `user_state` убедиться, что есть:  
  `selected_artifact_ids: list[int]`, `awaiting_ask_search: bool`, `ask_page: int`, `ask_page_msg_ids: list[int]`, `ask_footer_msg_id: int`, `ask_filter: dict`, `active_project_id`, `linked_project_ids`, `auto_clear_selection: bool`.  
  Если нет — **миграция** с `NOT NULL DEFAULT`.

---

## 1) Reply‑клавиатура → короткая (ASK‑центричная)

**Где:** модуль с Reply‑клавой.

- Оставить **3** кнопки: `⚙️ Actions | 💬 Chat ON/OFF | ❓ ASK‑WIZARD`.
- Кнопку **Memory** убрать (Memory будет внутри ASK).

**Проверка:** клавиатура всегда на экране, обновляется одной функцией.

---

## 2) ASK‑Home (дом мастера)

**Где:** `app/handlers/ask.py`

- Рендер домашней панели:  
  `🔍 Поиск` (ведёт на экран List), `Auto‑clear: ON/OFF`, `🧹 Сброс`, `📥 Import last`, плашка **Бюджет: ~N токенов**.
- Кнопка `❓ Ask` показывается **только если** `selected_artifact_ids` не пуст.

**Важно:** никаких вызовов LLM на этом экране.

---

## 3) Экран **List** = сердце мастера (включая поиск в шапке)

**Где:** `app/handlers/ask.py`

### 3.1 Шапка и ForceReply
- Кнопка `ask:list:search` рисует шапку и вызывает ForceReply:  
  _«Введи название, #тег или id»_.  
- В `ask_search_reply` (ответ на ForceReply): разобрать ввод:  
  `^\d+$ → mode=id`, `^#(.+)$ → mode=tag`, иначе `mode=name`.  
  Сохранить в `ask_filter`, `ask_page=1`. Удалить сообщение пользователя.

### 3.2 Тело (плашки)
- Показывать **ровно 5** артефактов, **каждый — отдельное сообщение** с текстом:  
  `N. <Название> [#tag1 #tag2] (id 689…, 2025‑09‑16)` (это **не кнопка**).
- Под каждой плашкой — **ОДНА строка** инлайн‑кнопок: `[➕/✅, 🗑]`.

### 3.3 Футер (пагинация)
- Отдельное футер‑сообщение: `⬅️  Стр. X/Y  ➡️`.  
- Колбэки `ask:prev` / `ask:next` пересобирают страницу.

### 3.4 Управление сообщениями
- В стейте хранить `ask_page_msg_ids` и `ask_footer_msg_id`.  
- При перерисовке **удалять** старые 5 плашек + футер, затем рисовать новые.

**Готовые фрагменты:**

```python
# --- парсер ---
import re
def _parse_search_query(text: str):
    q = (text or "").strip()
    if not q: return {"mode": None, "term": None, "artifact_id": None}
    if re.fullmatch(r"\d+", q): return {"mode":"id","artifact_id":int(q),"term":None}
    m = re.fullmatch(r"#\s*(.+)", q)
    if m: return {"mode":"tag","term":m.group(1).strip().lower(),"artifact_id":None}
    return {"mode":"name","term":q.lower(),"artifact_id":None}
```

```python
# --- тумблер клавиатуры под плашкой ---
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def _item_kb(artifact_id: int, selected: bool) -> InlineKeyboardMarkup:
    toggle = "✅" if selected else "➕"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=toggle, callback_data=f"ask:toggle:{artifact_id}"),
        InlineKeyboardButton(text="🗑", callback_data=f"ask:del:{artifact_id}"),
    ]])
```

```python
# --- мгновенный тумблер ---
@router.callback_query(F.data.startswith("ask:toggle:"))
async def ask_toggle(cb: CallbackQuery, state: FSMContext):
    aid = int(cb.data.split(":")[-1])
    data = await state.get_data()
    selected = set(data.get("selected_artifact_ids", []))
    selected.symmetric_difference_update({aid})
    await state.update_data(selected_artifact_ids=list(selected))
    await cb.message.edit_reply_markup(reply_markup=_item_kb(aid, aid in selected))
    await cb.answer("Обновлено")
```

---

## 4) Выборка из БД без дублей (Variant B)

**Где:** `app/services/memory.py` (или `services/artifacts.py`)

- Область проектов: `project_ids = [active_project_id] + linked_project_ids`.  
- **Подзапрос с `distinct id`** и всеми фильтрами (в т.ч. тег):  
  внешний запрос по `id in (sub)` + `order by created_at desc` + `limit/offset`.
- На UI‑уровне перед рендером — **страховка** уникализацией по `id`.

```python
# эскиз
sub = select(Artifact.id).where(Artifact.project_id.in_(project_ids))
if mode=="id": sub = sub.where(Artifact.id==artifact_id)
elif mode=="tag": sub = select(Artifact.id).join(ArtifactTag)...where(lower(Tag.name).like(f"%{term}%"))
elif mode=="name": sub = sub.where(lower(Artifact.title).like(f"%{term}%"))
sub = sub.distinct()
main = select(Artifact).where(Artifact.id.in_(sub)).order_by(Artifact.created_at.desc()).limit(5).offset((page-1)*5)
```

**Запрещено:** `DISTINCT ON` с неподходящим `ORDER BY`.

---

## 5) Linked‑проекты (правило блокировки правок состава)

**Где:** `ask.py` (бэйдж и подсказка), ваши хендлеры проектов.

- Если `linked_project_ids` не пуст — показывать бейдж «🔒 Linked: ON» и не давать менять **состав чатов проекта** (read‑only).  
- Выбор источников для запроса (`➕/✅`) **разрешён**, он не меняет каталог проекта.

---

## 6) Auto‑clear — семантика

**Где:** `ask.py`

- Переключатель `Auto‑clear: ON/OFF` влияет **только** на поведение после нажатия `❓ Ask`:  
  ON — `selected_artifact_ids` очищается; OFF — список остаётся.

---

## 7) НОЛЬ LLM в этом потоке

- Проверить, что `ask:list`, `ask:search`, `ask:prev/next`, `ask:toggle`, `ask:del` **никогда** не зовут LLM.  
- Catch‑all молчит, если `awaiting_ask_search=True`.

---

## 8) Чек‑лист приёмки

1) Reply‑клава: `Actions | Chat | ASK‑WIZARD` (без Memory).  
2) ASK‑Home отрисован, `❓ Ask` виден только при выбранных источниках, бюджет отображается.  
3) List: 5 плашек; под каждой **одна строка** `➕/✅, 🗑`.  
4) `plan` → ищет по названию; `#woman` → по тегам; `12` → по id. Chat: OFF; LLM не дергается.  
5) `➕` меняется на `✅` мгновенно; обратный клик — обратно.  
6) `⬅️/➡️` всегда держат на экране ровно 5 плашек; старые сообщения и футер удаляются.  
7) Linked: при активной линковке нет возможности править состав чатов проекта (только выбирать для запроса).  
8) `Auto‑clear: ON` — после настоящего `❓ Ask` выбор очищается; при OFF — остаётся.

---

## 9) Подсказки по отладке (логи)

- В `ask_search_reply`: лог `mode/term/id`, `project_ids`, `user_id`.  
- В `list_artifacts`: финальные `project_ids`, `mode`, `term`, `limit/offset`, количество id до/после пагинации.  
- Если пусто — почти всегда `project_ids` пустой или `mode=None`.

---

Готово. Выполнять шаги последовательно, мерджить **маленькими PR**. Вопросы — в комментариях к задачам.
