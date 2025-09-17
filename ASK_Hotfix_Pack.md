# ASK Hotfix Pack — LLM 400, Question Persistence, Prompt Cleanup, Sources/Refine, No '.'

Ниже — готовые вставки **с кодом**. Скопируй в проект строго по разделам:
- `app/services/llm.py` — нормализация параметров (температура/лимиты токенов) → лечит 400 `unsupported_value`.
- `app/handlers/ask.py` — чат‑тоггл без «точек», сохранение вопроса в чате, очистка ForceReply‑сообщений, корректные `Sources/Back`, `Refine`, `Delete` (вопрос+ответ).

> Важно: в твоём проекте имена функций могут отличаться. Если они другие — вставляй тело в соответствующие хэндлеры/сервисы. Все места пометил комментами `# HOTFIX:`.

---

## 1) `app/services/llm.py` — нормализация параметров

```python
# HOTFIX: put near your OpenAI call builder
TEMPERATURE_SUPPORTED = (
    # классические чаты поддерживают кастомную температуру
    "gpt-3.5-turbo", "gpt-4", "gpt-4-turbo", "gpt-4o"
)

REASONING_FAMILY = ("o1", "o3", "o4", "o5", "gpt-5", "gpt-5-thinking")

def _model_caps(model: str) -> dict:
    m = model.lower()
    caps = {"use_temperature": False, "tokens_param": "max_tokens"}
    if any(m.startswith(x) for x in TEMPERATURE_SUPPORTED):
        caps["use_temperature"] = True
        caps["tokens_param"] = "max_tokens"
    if any(m.startswith(x) for x in REASONING_FAMILY):
        # reasoning-модели требуют max_completion_tokens и часто игнорируют temperature
        caps["use_temperature"] = False
        caps["tokens_param"] = "max_completion_tokens"
    return caps

def build_openai_payload(model: str, messages: list, *, temperature: float|None, max_tokens: int|None):
    caps = _model_caps(model)
    payload = {"model": model, "messages": messages}

    # лимит токенов: правильное имя параметра
    if max_tokens:
        payload[caps["tokens_param"]] = max_tokens

    # температура — только для семейства, где это поддерживается
    if caps["use_temperature"] and temperature is not None and temperature != 1:
        payload["temperature"] = float(temperature)
    # иначе — вообще не добавляем ключ 'temperature'

    return payload
```

Пример использования в твоём сервисе перед вызовом OpenAI:
```python
# HOTFIX: replace manual dict with helper
payload = build_openai_payload(
    model=cfg.model,
    messages=messages,
    temperature=cfg.temperature,     # можно оставить 0.7 — хелпер сам отбросит
    max_tokens=cfg.max_tokens or 1024
)
resp = await client.chat.completions.create(**payload)
```

---

## 2) `app/handlers/ask.py` — точечные замены

### 2.1. Включение чата — без «точек», удаляем подсказку, сразу ForceReply

```python
# HOTFIX: callback ask:chat:on
@router.callback_query(F.data == "ask:chat:on")
async def ask_toggle_chat(cb: CallbackQuery, state: FSMContext):
    st = await state.get_data()
    await state.update_data(chat_on=True)

    # удалить старое сообщение "Включи чат..."
    with contextlib.suppress(Exception):
        await cb.message.delete()

    # показать ForceReply и запомнить message_id для последующей очистки
    prompt = await cb.message.answer("Введите вопрос…", reply_markup=ForceReply(selective=True))
    await state.update_data(ask_prompt_msg_id=prompt.message_id)
    await cb.answer()
```

### 2.2. Приём вопроса — НЕ удалять вопрос пользователя; удалить только ForceReply‑промпт

```python
# HOTFIX: handler of user's question (Ask)
@router.message(AskStates.awaiting_question)
async def ask_receive_question(message: Message, state: FSMContext):
    data = await state.get_data()
    # 1) убрать ForceReply-подсказку, сам вопрос пользователя не трогаем
    with contextlib.suppress(Exception):
        pid = data.get("ask_prompt_msg_id")
        if pid:
            await message.bot.delete_message(message.chat.id, pid)

    # 2) статус "Готовлю ответ…"
    prep = await message.answer("Готовлю ответ…")

    # 3) запуск пайплайна LLM
    selected_ids = data.get("selected_artifact_ids") or []
    text, run_id, used_source_ids = await run_llm_pipeline(
        user_id=message.from_user.id,
        question=message.text or "",
        source_ids=selected_ids
    )

    # 4) запомнить id вопроса и ответа — понадобятся для Delete/Refine/Sources
    await state.update_data(
        last_answer={
            "run_id": run_id,
            "question_msg_id": message.message_id,
            "answer_msg_id": prep.message_id,
            "source_ids": used_source_ids,
            "saved": False,
            "pinned": False,
        }
    )

    # 5) отрендерить итог и прикрутить панель действий (не забываем передавать её при любом edit)
    kb = answer_actions_kb(run_id, saved=False, pinned=False)
    await message.bot.edit_message_text(
        chat_id=prep.chat.id,
        message_id=prep.message_id,
        text=text + "\n\n" + f"📚 Sources: " + ", ".join(f"id{sid}" for sid in used_source_ids),
        reply_markup=kb
    )
```

### 2.3. Источники — оверлей клавиатуры + «Назад» без изменения текста ответа

```python
# HOTFIX: sources overlay
@router.callback_query(F.data.startswith("ask:answer:sources:"))
async def answer_sources(cb: CallbackQuery, state: FSMContext):
    run_id = cb.data.split(":")[-1]
    data = await state.get_data()
    la = (data.get("last_answer") or {})
    if la.get("run_id") != run_id:
        return await cb.answer("Источники недоступны", show_alert=True)
    src = la.get("source_ids") or []
    kb = InlineKeyboardBuilder()
    for sid in src[:6]:
        kb.button(text=f"id{sid}", callback_data="noop")
    kb.button(text="↩️", callback_data=f"ask:answer:sources:back:{run_id}")
    kb.adjust(3,3,1)
    await cb.message.edit_reply_markup(reply_markup=kb.as_markup())
    await cb.answer()

@router.callback_query(F.data.startswith("ask:answer:sources:back:"))
async def answer_sources_back(cb: CallbackQuery):
    run_id = cb.data.split(":")[-1]
    await cb.message.edit_reply_markup(reply_markup=answer_actions_kb(run_id, saved=True, pinned=False))
    await cb.answer()
```

### 2.4. Refine — открыть ForceReply и **не** терять исходный вопрос

> Telegram не позволяет «предзаполнить» поле ввода. Поэтому показываем ForceReply **и** дублируем исходный текст над ним.

```python
# HOTFIX: refine open
@router.callback_query(F.data.startswith("ask:answer:refine:"))
async def answer_refine(cb: CallbackQuery, state: FSMContext):
    run_id = cb.data.split(":")[-1]
    data = await state.get_data()
    la = data.get("last_answer") or {}
    if la.get("run_id") != run_id:
        return await cb.answer("Нет контекста", show_alert=True)

    q_mid = la.get("question_msg_id")
    if q_mid:
        try:
            q_msg = await cb.message.bot.forward_message(cb.message.chat.id, cb.message.chat.id, q_mid)
            await cb.message.bot.delete_message(cb.message.chat.id, q_msg.message_id)
        except Exception:
            pass

    tip = await cb.message.answer("Уточните запрос (смотрите исходный текст выше)…", reply_markup=ForceReply(selective=True))
    await state.update_data(ask_prompt_msg_id=tip.message_id, refine_for_run=run_id)
    await cb.answer()
```

### 2.5. Delete — удалить и ответ, и вопрос, и подвисшие ForceReply‑подсказки

```python
# HOTFIX: delete confirm
@router.callback_query(F.data.startswith("ask:answer:delete:confirm:"))
async def answer_delete_confirm(cb: CallbackQuery, state: FSMContext):
    run_id = cb.data.split(":")[-1]
    data = await state.get_data()
    la = data.get("last_answer") or {}
    if la.get("run_id") != run_id:
        return await cb.answer("Нет контекста", show_alert=True)

    # удалить ответ
    with contextlib.suppress(Exception):
        await cb.message.delete()

    # удалить исходный вопрос
    q_mid = la.get("question_msg_id")
    if q_mid:
        with contextlib.suppress(Exception):
            await cb.message.bot.delete_message(cb.message.chat.id, q_mid)

    # удалить возможные ForceReply‑подсказки
    pid = data.get("ask_prompt_msg_id")
    if pid:
        with contextlib.suppress(Exception):
            await cb.message.bot.delete_message(cb.message.chat.id, pid)

    # очистить состояние
    await state.update_data(last_answer=None, ask_prompt_msg_id=None, refine_for_run=None)
    await cb.answer("Удалено")
```

### 2.6. Панель действий под ответом (иконки‑только), не исчезает при редактировании

```python
# HOTFIX: единая панель
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

## 3) Поведение панелей (чтобы не плодить «ASK панель» в чате)

- На `Сброс`/`Auto‑clear` **редактируй** существующее сообщение c панелью (`edit_text/edit_reply_markup`), **не** отправляй новое.
- Любые подсказки/уведомления — `answerCallbackQuery`, без новых сообщений.

---

## 4) Быстрый чек‑лист после применения

- Chat: OFF → подсказка с инлайн‑кнопкой; после клика подсказка **удаляется**, приходит ForceReply.
- Сообщение пользователя остаётся в чате. ForceReply‑промпт — удаляется после получения вопроса.
- Ответ приходит одним сообщением, с панелью `💾📌🧾🔁📚🗑`. Кнопка `📚` показывает оверлей и `↩️ Back`.
- `🔁 Refine` открывает ForceReply, исходный текст видно (показан/сохранён выше), ничего лишнего не остаётся подвисшим.
- `🗑 Delete` удаляет и ответ, и исходный вопрос, и любые подсказки.
- Ошибка `Unsupported value: 'temperature'` исчезла.
