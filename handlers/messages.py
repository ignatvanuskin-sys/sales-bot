"""Этап 4: генерация сообщений для холодного контакта + редактирование перед копированием.

HTML-спецсимволы (&, <, >) экранируются только в пользовательских/LLM-данных.
HTML-разметка эмодзи (E.*) вставляется нами и НЕ экранируется.
"""

import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import settings
from db import repo
from db.base import session_factory
from keyboards.main_menu import messages_kb
from services.ai import AIError, AIOverloadError, AIRateLimitError, generate_messages
from states.fsm import EditMessageFSM
from utils.emoji_config import E, P
from utils.fsm_input import get_text_input
from utils.idempotency import IdempotencyLock
from utils.safe_send import safe_answer, safe_edit

logger = logging.getLogger(__name__)
router = Router(name="messages")

MSG_RATE_LIMIT = f"{E.TIMER} Слишком много запросов к AI. Попробуй через минуту."
MSG_OVERLOADED = f"{E.TIMER} Бесплатная модель сейчас перегружена. Попробуй через минуту."
MSG_AI_FAILED = f"{E.CROSS} Не получилось сгенерировать сообщения. Попробуй ещё раз чуть позже."
MSG_BUDGET = f"{E.TIMER} Достигнут дневной лимит AI-вызовов. Попробуй завтра."


def render_variants(name: str, short: str, long: str) -> str:
    """Текст с двумя вариантами. Всё пользовательское/LLM-содержимое экранируется."""
    return (
        f"{E.COMMENT} Сообщения для <b>{escape(name)}</b>\n\n"
        f"<b>Короткое:</b>\n<pre>{escape(short)}</pre>\n\n"
        f"<b>Развёрнутое:</b>\n<pre>{escape(long)}</pre>\n\n"
        f"{E.IDEA} Текст в рамках можно копировать нажатием. Или отредактируй вариант ниже."
    )


@router.callback_query(F.data.startswith("gen:"))
async def generate_for_lead(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(f"{P.CROSS} Некорректные данные. Попробуй ещё раз.", show_alert=True)
        return

    async with IdempotencyLock("generation", callback.from_user.id, lead_id, ttl=120) as acquired:
        if not acquired:
            await callback.answer(f"{E.TIMER} Генерация уже выполняется.", show_alert=True)
            return
        await _generate_for_lead_locked(callback, state, lead_id)


async def _generate_for_lead_locked(
    callback: CallbackQuery, state: FSMContext, lead_id: int
) -> None:
    async with session_factory() as session:
        lead = await repo.get_lead(session, lead_id, callback.from_user.id)
    if lead is None:
        await callback.answer(f"{P.CROSS} Лид не найден.", show_alert=True)
        return
    if not lead.ai_analysis:
        await callback.answer(
            f"{P.CHART} Сначала сделай AI-анализ этого лида.", show_alert=True
        )
        return

    await callback.answer()
    status = await safe_answer(callback.message, f"{E.WRITING} Генерирую варианты сообщений…")

    # P-2: Проверка дневного лимита LLM-вызовов
    async with session_factory() as session:
        allowed, _count = await repo.check_llm_budget(
            session, settings.llm_daily_limit, callback.from_user.id, "generation"
        )
    if not allowed:
        await safe_edit(status, MSG_BUDGET)
        return

    try:
        short, long = await generate_messages(lead.name, lead.ai_analysis)
    except AIRateLimitError:
        await safe_edit(status, MSG_RATE_LIMIT)
        return
    except AIOverloadError:
        await safe_edit(status, MSG_OVERLOADED)
        return
    except AIError as exc:
        logger.error("Message generation failed for lead=%s: %s", lead_id, exc)
        await safe_edit(status, MSG_AI_FAILED)
        return

    # Audit: messages generated successfully
    async with session_factory() as session:
        await repo.log_action(session, callback.from_user.id, "messages_generated", lead_id)

    await state.update_data(gen_short=short, gen_long=long, gen_lead_id=lead_id, gen_lead_name=lead.name)
    await safe_edit(status, render_variants(lead.name, short, long), reply_markup=messages_kb(lead_id))


@router.callback_query(F.data.startswith("edm:"))
async def edit_message_start(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[2] not in ("short", "long"):
        await callback.answer(f"{P.CROSS} Некорректные данные. Попробуй ещё раз.", show_alert=True)
        return
    try:
        lead_id = int(parts[1])
    except ValueError:
        await callback.answer(f"{P.CROSS} Некорректные данные. Попробуй ещё раз.", show_alert=True)
        return

    data = await state.get_data()
    if data.get("gen_lead_id") != lead_id:
        await callback.answer(f"{P.RELOAD} Сначала сгенерируй сообщения заново.", show_alert=True)
        return

    await state.set_state(EditMessageFSM.waiting_text)
    await state.update_data(edit_which=parts[2])
    which_label = "короткого" if parts[2] == "short" else "развёрнутого"
    await safe_answer(callback.message, f"{E.WRITING} Пришли новый текст {which_label} сообщения:")
    await callback.answer()


@router.message(EditMessageFSM.waiting_text)
async def edit_message_received(message: Message, state: FSMContext) -> None:
    # F3: фото/стикер раньше давали «Пустой текст...» без объяснения и выхода.
    new_text = await get_text_input(message, "Пустой текст не подойдёт. Пришли текст сообщения.")
    if new_text is None:
        return

    data = await state.get_data()
    which = data.get("edit_which", "short")
    lead_id = data.get("gen_lead_id")
    name = data.get("gen_lead_name", "")
    short = new_text if which == "short" else data.get("gen_short", "")
    long = new_text if which == "long" else data.get("gen_long", "")

    await state.set_state(None)
    await state.update_data(gen_short=short, gen_long=long, edit_which=None)
    await safe_answer(message, render_variants(name, short, long), reply_markup=messages_kb(lead_id))
