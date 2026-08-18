"""Настройки Chat Monitor через кнопки бота."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from chat_monitor.config_store import (
    ChatMonitorConfig,
    parse_chat_refs,
    parse_min_score,
)
from config import settings
from db import repo
from db.base import session_factory
from keyboards.main_menu import chat_monitor_chats_kb, chat_monitor_kb
from states.fsm import ChatMonitorFSM
from utils.emoji_config import E, P
from utils.fsm_input import get_text_input
from utils.safe_send import safe_answer, safe_edit

router = Router(name="chat_monitor")

MSG_BAD_DATA = f"{P.CROSS} Некорректные данные. Попробуй ещё раз."


def _env_default_chats() -> list[str]:
    return [str(chat) for chat in settings.chat_monitor_chat_list]


async def _get_config(owner_tg_id: int) -> ChatMonitorConfig:
    async with session_factory() as session:
        row = await repo.get_or_create_chat_monitor_settings(
            session,
            owner_tg_id,
            default_chats=_env_default_chats(),
            default_min_score=settings.chat_monitor_min_score,
        )
        return repo.chat_monitor_settings_to_config(row)


def _runner_status_line(owner_tg_id: int) -> str:
    if not settings.chat_monitor_ready:
        return f"{E.WARNING} Telethon .env заполнен не полностью — runner не стартует."
    if settings.chat_monitor_owner_tg_id != owner_tg_id:
        return (
            f"{E.WARNING} Этот Telegram id не совпадает с CHAT_MONITOR_OWNER_TG_ID. "
            "Настройки сохранятся, но runner их не использует."
        )
    return f"{E.CHECK} Telethon .env готов. Runner использует настройки этого аккаунта."


def _format_chat_monitor_menu(config: ChatMonitorConfig) -> str:
    enabled = "включён" if config.is_enabled else "выключен"
    chat_count = len(config.chats)
    lines = [
        "<b>Chat Monitor</b>",
        f"{E.PIN} Статус: {enabled}",
        f"{E.COMMENT} Чатов: {chat_count}",
        f"{E.TARGET} Threshold: {config.min_score:.2f}",
        "",
        _runner_status_line(config.owner_tg_id),
        "",
        "Через бота настраиваются только чаты, threshold и включение. "
        "API_ID/API_HASH/PHONE/session остаются в .env и не вводятся в Telegram-чат.",
    ]
    return "\n".join(lines)


def _format_chats(chats: list[str]) -> str:
    if not chats:
        return f"{E.INFO} Чаты для мониторинга пока не добавлены."
    lines = ["<b>Чаты Chat Monitor</b>"]
    lines.extend(f"{i + 1}. <code>{escape(chat)}</code>" for i, chat in enumerate(chats))
    return "\n".join(lines)


@router.callback_query(F.data == "menu:chat_monitor")
async def show_chat_monitor_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    config = await _get_config(callback.from_user.id)
    await safe_answer(
        callback.message,
        _format_chat_monitor_menu(config),
        reply_markup=chat_monitor_kb(config.is_enabled, has_chats=bool(config.chats)),
    )
    await callback.answer()


@router.callback_query(F.data == "cm:chats")
async def show_chat_monitor_chats(callback: CallbackQuery) -> None:
    config = await _get_config(callback.from_user.id)
    await safe_answer(
        callback.message,
        _format_chats(config.chats),
        reply_markup=chat_monitor_chats_kb(config.chats),
    )
    await callback.answer()


@router.callback_query(F.data == "cm:add")
async def add_chat_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ChatMonitorFSM.waiting_chat)
    await safe_answer(
        callback.message,
        f"{E.COMMENT} Пришли username или chat_id открытого чата.\n\n"
        "Можно несколько через запятую или с новой строки:\n"
        "<code>@open_nails_chat, -1001234567890</code>",
    )
    await callback.answer()


@router.message(ChatMonitorFSM.waiting_chat)
async def add_chat_received(message: Message, state: FSMContext) -> None:
    # F3: фото/стикер раньше давали «Не нашёл chat username/id...» без объяснения и выхода.
    raw = await get_text_input(message, "Не нашёл chat username/id. Пришли @username или числовой chat_id")
    if raw is None:
        return
    if len(raw) > 4096:
        await safe_answer(message, "Список чатов слишком длинный (максимум 4096 символов).\n\nИли пришли /cancel для отмены.")
        return
    refs = parse_chat_refs(raw, max_refs=settings.chat_monitor_max_chats)
    if not refs:
        await safe_answer(message, "Не нашёл chat username/id. Пришли @username или числовой chat_id.\n\nИли пришли /cancel для отмены.")
        return
    async with session_factory() as session:
        row = await repo.add_chat_monitor_chats(
            session,
            message.from_user.id,
            refs,
            default_chats=_env_default_chats(),
            default_min_score=settings.chat_monitor_min_score,
            max_chats=settings.chat_monitor_max_chats,
        )
        config = repo.chat_monitor_settings_to_config(row)
    await state.clear()
    await safe_answer(
        message,
        f"{E.CHECK} Добавлено: {', '.join(escape(ref) for ref in refs)}\n\n"
        + _format_chat_monitor_menu(config),
        reply_markup=chat_monitor_kb(config.is_enabled, has_chats=bool(config.chats)),
    )


@router.callback_query(F.data.startswith("cm:del:"))
async def delete_chat(callback: CallbackQuery) -> None:
    try:
        index = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    async with session_factory() as session:
        row = await repo.delete_chat_monitor_chat(session, callback.from_user.id, index)
        if row is None:
            await callback.answer("Такого чата уже нет.", show_alert=True)
            return
        config = repo.chat_monitor_settings_to_config(row)
    await safe_edit(
        callback.message,
        _format_chats(config.chats),
        reply_markup=chat_monitor_chats_kb(config.chats),
    )
    await callback.answer(f"{P.CHECK} Чат удалён")


@router.callback_query(F.data == "cm:threshold")
async def threshold_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ChatMonitorFSM.waiting_threshold)
    await safe_answer(
        callback.message,
        f"{E.TARGET} Пришли threshold от 0 до 1, например <code>0.7</code>.\n"
        "Лиды со score ниже threshold сохраняться не будут.",
    )
    await callback.answer()


@router.message(ChatMonitorFSM.waiting_threshold)
async def threshold_received(message: Message, state: FSMContext) -> None:
    # F3: фото/стикер раньше давали «Порог должен быть числом...» без объяснения и выхода.
    text = await get_text_input(message, "Порог должен быть числом от 0 до 1, например 0.7")
    if text is None:
        return
    value = parse_min_score(text)
    if value is None:
        await safe_answer(message, "Порог должен быть числом от 0 до 1, например 0.7.\n\nИли пришли /cancel для отмены.")
        return
    async with session_factory() as session:
        row = await repo.set_chat_monitor_min_score(session, message.from_user.id, value)
        config = repo.chat_monitor_settings_to_config(row)
    await state.clear()
    await safe_answer(
        message,
        f"{E.CHECK} Threshold обновлён: {value:.2f}\n\n" + _format_chat_monitor_menu(config),
        reply_markup=chat_monitor_kb(config.is_enabled, has_chats=bool(config.chats)),
    )


@router.callback_query(F.data == "cm:toggle")
async def toggle_monitor(callback: CallbackQuery) -> None:
    async with session_factory() as session:
        row = await repo.toggle_chat_monitor_enabled(session, callback.from_user.id)
        config = repo.chat_monitor_settings_to_config(row)
    await safe_edit(
        callback.message,
        _format_chat_monitor_menu(config),
        reply_markup=chat_monitor_kb(config.is_enabled, has_chats=bool(config.chats)),
    )
    await callback.answer(f"{P.CHECK} Статус обновлён")


@router.callback_query(F.data == "cm:help")
async def chat_monitor_help(callback: CallbackQuery) -> None:
    config = await _get_config(callback.from_user.id)
    await safe_answer(
        callback.message,
        f"{E.INFO} Как запустить Chat Monitor:\n\n"
        "1. Убедись, что в .env заполнены CHAT_MONITOR_API_ID, "
        "CHAT_MONITOR_API_HASH, CHAT_MONITOR_PHONE и CHAT_MONITOR_SESSION_PATH.\n"
        "2. Добавь нужные открытые чаты кнопкой «Добавить чат».\n"
        "3. Перезапусти основной bot.py: Chat Monitor запускается внутри него. "
        "Отдельный процесс запускать нельзя.\n\n"
        "Код Telethon обычно приходит не SMS, а в официальный Telegram-клиент. "
        "Проверь Telegram на этом номере, папку Archived/Service Notifications и формат телефона +7...",
        reply_markup=chat_monitor_kb(config.is_enabled, has_chats=bool(config.chats)),
    )
    await callback.answer()
