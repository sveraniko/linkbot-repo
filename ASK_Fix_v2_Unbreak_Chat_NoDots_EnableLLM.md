
# ASK Fix v2 — Unbreak Chat, kill “...”, enable LLM (with code)

> Цель: вернуть рабочий чат‑гейт, убрать «…» навсегда и снова звать LLM. Готовые вставки/замены ниже. Вставляй их в `app/handlers/ask.py` и `app/services/llm.py`.

---

## 0) Конфиг (в одном месте)

```python
# app/config.py (или где у тебя конфиг)
import os

LLM_DISABLED = os.getenv("LLM_DISABLED", "0") == "1"
ANSWER_BAR_WRAP = os.getenv("ANSWER_BAR_WRAP", "auto")  # auto|1|2
ANSWER_BAR_ICONS_ONLY = os.getenv("ANSWER_BAR_ICONS_ONLY", "1") == "1"
```
---

## 1) Router порядок (важно, иначе коллбэки не ловятся)

В файле, где подключаешь роутеры, **ASK должен быть выше** любых «общих»/template‑роутеров, а `catch‑all` — последним:

```python
# app/bot.py (пример)
from app.handlers.ask import ASK
# from app.handlers.templates import TEMPLATES  # пример
# ...

dp.include_router(ASK)          # 1) ASK
# dp.include_router(TEMPLATES)  # 2) другие панели/модули
# dp.include_router(CATCH_ALL)  # 3) catch-all последним
```
---

## 2) Chat ON через inline‑кнопку (без «точек») — **замена хендлера**

```python
# app/handlers/ask.py
from aiogram import F
from aiogram.types import CallbackQuery, ForceReply
import contextlib

@ASK.callback_query(F.data == "ask:chat:on")
async def ask_chat_on(cb: CallbackQuery, state: FSMContext):
    # включаем флаг в стейте
    await state.update_data(chat_on=True)

    # удаляем подсказку "Включи чат..." (не оставляем хвостов)
    with contextlib.suppress(Exception):
        await cb.message.delete()

    # выдаём ForceReply "Введите вопрос…" и запоминаем id промпта
    prompt = await cb.message.answer("Введите вопрос…", reply_markup=ForceReply(selective=True))
    await state.update_data(ask_prompt_msg_id=prompt.message_id, awaiting_ask_question=True)

    await cb.answer()  # только попап, без новых сообщений
```
---

## 3) Chat ON/OFF через reply‑клавиатуру (опционально)

```python
# app/handlers/ask.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def reply_kb(chat_on: bool) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⚙️ Actions"),
                   KeyboardButton(text=f"💬 Chat: {'ON' if chat_on else 'OFF'}"),
                   KeyboardButton(text="❓ ASK-WIZARD")]],
        resize_keyboard=True
    )

@ASK.message(F.text.regexp(r"^💬 Chat: (ON|OFF)$"))
async def toggle_chat_reply_button(msg: Message, state: FSMContext):
    data = await state.get_data()
    new_state = not bool(data.get("chat_on"))
    await state.update_data(chat_on=new_state)
    # обновляем клавиатуру одним новым сообщением и забываем старое
    m = await msg.answer(f"Чат: {'включён' if new_state else 'выключен'}", reply_markup=reply_kb(new_state))
    with contextlib.suppress(Exception):
        await msg.delete()
    # это служебное сообщение можно удалить через 3–5 сек (если есть TTL‑удалялка)
```
---

## 4) Приём вопроса — **не удаляем** сообщение пользователя, убираем только ForceReply, зовём LLM

```python
# app/handlers/ask.py
from aiogram.fsm.context import FSMContext
import time

@ASK.message()
async def ask_question_receiver(msg: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("awaiting_ask_question"):
        return

    # убрать ForceReply‑подсказку
    with contextlib.suppress(Exception):
        pid = data.get("ask_prompt_msg_id")
        if pid:
            await msg.bot.delete_message(msg.chat.id, pid)

    # гейт: чат должен быть ON
    if not data.get("chat_on"):
        await msg.answer("Включи чат кнопкой внизу или через ASK‑панель.")
        return

    # подготовка ответа (одно сообщение, потом редактируем)
    prep = await msg.answer("Готовлю ответ…")

    # собрать выбранные источники
    selected_ids = data.get("selected_artifact_ids") or []
    if not selected_ids:
        await msg.bot.edit_message_text(chat_id=prep.chat.id, message_id=prep.message_id,
                                        text="Выбери источники в List.")
        await state.update_data(awaiting_ask_question=False)
        return

    # Вызов LLM (убери тестовый заглушечный «LLM отключён»)
    from app.config import LLM_DISABLED
    if LLM_DISABLED:
        answer_text = "LLM временно отключён админом."
        run_id = f"stub-{int(time.time())}"
        used = list(selected_ids)
    else:
        answer_text, run_id, used = await run_llm_pipeline(
            user_id=msg.from_user.id,
            question=msg.text or "",
            source_ids=selected_ids
        )

    # сохранить контекст ответа для кнопок
    await state.update_data(
        awaiting_ask_question=False,
        last_answer={
            "run_id": run_id,
            "question_msg_id": msg.message_id,
            "answer_msg_id": prep.message_id,
            "source_ids": used,
            "saved": False,
            "pinned": False,
        }
    )

    # показать итог и панель (иконки‑только), панели не исчезают при edit
    kb = answer_actions_kb(run_id, saved=False, pinned=False)
    await msg.bot.edit_message_text(
        chat_id=prep.chat.id,
        message_id=prep.message_id,
        text=answer_text + "\n\n" + "📚 Sources: " + ", ".join(f"id{sid}" for sid in used),
        reply_markup=kb
    )
```
---

## 5) Убить «…» во всех местах

**Ищи и удаляй** такие вызовы (они же спамят точками):
- `await message.answer(".")`
- `await cb.message.answer(".")`
- любой helper `send_status("...")` / `send_dots()`

Вместо этого:
- Используй `await cb.answer("Готово")` для короткой подсказки;  
- Или редактируй существующее сообщение (`edit_message_text / edit_reply_markup`).

**Быстрая проверка:** по проекту выполнить grep:  
`rg -n 'answer\("\."\)' app` и `rg -n '"\."' app`

---

## 6) Answer‑бар — не пропадает

Любой `edit_message_text` по сообщению ответа должен **обязательно** передавать `reply_markup=answer_actions_kb(...)`.  
Если этого не сделать хотя бы раз — Telegram снимет клавиатуру, и «кнопки пропали».

```python
def answer_actions_kb(run_id: str, *, saved: bool, pinned: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=("✅" if saved else "💾"), callback_data=f"ask:answer:save:{run_id}")
    kb.button(text=("📍" if pinned else "📌"), callback_data=f"ask:answer:pin:{run_id}")
    kb.button(text="🧾", callback_data=f"ask:answer:summary:{run_id}")
    kb.button(text="🔁", callback_data=f"ask:answer:refine:{run_id}")
    kb.button(text="📚", callback_data=f"ask:answer:sources:{run_id}")
    kb.button(text="🗑", callback_data=f"ask:answer:delete:{run_id}")
    kb.adjust(6)
    return kb.as_markup()
```
---

## 7) LLM payload нормализация (OpenAI 400 fix)

Если ловил `Unsupported value: 'temperature'` или «не тот параметр max_tokens», добавь хелпер:

```python
# app/services/llm.py
TEMPERATURE_SUPPORTED = ("gpt-3.5-turbo", "gpt-4", "gpt-4-turbo", "gpt-4o")
REASONING_FAMILY = ("o1", "o3", "o4", "o5", "gpt-5", "gpt-5-thinking")

def _model_caps(model: str) -> dict:
    m = model.lower()
    caps = {"use_temperature": False, "tokens_param": "max_tokens"}
    if any(m.startswith(x) for x in TEMPERATURE_SUPPORTED):
        caps["use_temperature"] = True
    if any(m.startswith(x) for x in REASONING_FAMILY):
        caps["use_temperature"] = False
        caps["tokens_param"] = "max_completion_tokens"
    return caps

def build_openai_payload(model: str, messages: list, *, temperature: float|None, max_tokens: int|None):
    caps = _model_caps(model)
    payload = {"model": model, "messages": messages}
    if max_tokens:
        payload[caps["tokens_param"]] = max_tokens
    if caps["use_temperature"] and temperature is not None and temperature != 1:
        payload["temperature"] = float(temperature)
    return payload
```
Используй его перед вызовом провайдера.

---

## 8) Acceptance — быстро прогнать

- Нажимаю inline «Включить чат» → подсказка исчезает, появляется ForceReply «Введите вопрос…» (без «…»).
- Пишу вопрос → ForceReply удалён, моё сообщение **осталось**; «Готовлю ответ…» → заменяется на текст ответа + панель иконок; `📚` — оверлей, `↩️ Back` возвращает панель.
- Тумблеры/пагинация/поиск — не создают новых сообщений; максимум — всплывашка.
- LLM реально зовётся (если `LLM_DISABLED=0`) — нет заглушки «TEST: LLM отключён`.
