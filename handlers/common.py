"""Глобальные команды и fallback-хендлеры."""

import logging
import time
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import settings
from db import repo
from db.base import session_factory
from db.models import STATUS_LABELS
from keyboards.main_menu import leads_filter_kb, main_menu_kb
from states.fsm import SearchFSM
from utils.emoji_config import E
from utils.safe_send import safe_answer

commands_router = Router(name="commands")
fallback_router = Router(name="fallback")

logger = logging.getLogger(__name__)

# Время старта бота (для расчета uptime)
_bot_start_time = time.time()

STATS_HELP_LINE = "/stats — статистика лидов и AI-расходов\n"

HELP_TEXT = (
    f"{E.INFO} Я AI Sales Agent: ищу компании, сохраняю лиды в CRM, "
    "делаю AI-анализ и помогаю подготовить первое сообщение.\n\n"
    "Команды:\n"
    "/start — открыть главное меню\n"
    "/search — быстрый поиск компаний\n"
    "/leads — открыть мои лиды\n"
    "/menu — вернуться в главное меню\n"
    "/stats — статистика лидов и AI-расходов\n"
    "/health — проверка состояния бота\n"
    "/cancel — отменить текущий сценарий\n\n"
    "Основные действия доступны кнопками в меню."
)

FALLBACK_TEXT = (
    f"{E.INFO} Я не нашёл подходящего действия.\n\n"
    "Используй кнопки в меню или команду /help, чтобы посмотреть возможности бота."
)

STALE_SEARCH_CARD_TEXT = "Эта карточка устарела, начните новый поиск."


@commands_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await safe_answer(message, HELP_TEXT, reply_markup=main_menu_kb())


@commands_router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    """Проверка состояния бота (MONITORING-2).
    
    Показывает:
    - Uptime (время работы)
    - Статус БД
    - Статус Redis (если настроен)
    - Базовые метрики
    """
    lines = [f"<b>{E.CHECK} Health Check</b>\n"]
    
    # Uptime
    uptime_seconds = time.time() - _bot_start_time
    uptime_hours = int(uptime_seconds // 3600)
    uptime_minutes = int((uptime_seconds % 3600) // 60)
    lines.append(f"<b>Uptime:</b> {uptime_hours}ч {uptime_minutes}м")
    
    # БД connectivity
    try:
        async with session_factory() as session:
            count = await repo.count_today_llm_calls(session, message.from_user.id)
        lines.append(f"<b>База данных:</b> {E.CHECK} OK")
        lines.append(f"  • LLM вызовов сегодня: {count}")
    except Exception as exc:
        # SEC-FIX: юзеру — только факт ошибки, без деталей (путь/запрос/схема).
        # Подробности — в лог, а не в чат.
        logger.error("Health check: DB error: %s", exc)
        lines.append(f"<b>База данных:</b> {E.CROSS} ERROR")
    
    # Redis status
    if settings.redis_url:
        try:
            from redis.asyncio import Redis
            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            await redis.ping()
            lines.append(f"<b>Redis:</b> {E.CHECK} Connected")
            await redis.close()
        except Exception:
            lines.append(f"<b>Redis:</b> {E.CROSS} Unavailable")
    else:
        lines.append("<b>Redis:</b> Not configured")
    
    # LLM Provider
    lines.append(f"\n<b>LLM Provider:</b> {settings.llm_provider}")
    if settings.llm_daily_limit > 0:
        lines.append(f"<b>Daily Limit:</b> {settings.llm_daily_limit}")
    
    # Timestamp
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lines.append(f"\n<b>Timestamp (UTC):</b> {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    await safe_answer(message, "\n".join(lines))


@commands_router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Статистика лидов, источников, конверсии и LLM-расходов (AD-1)."""
    async with session_factory() as session:
        stats = await repo.get_stats(session, message.from_user.id)

    total = stats["total"]
    if total == 0:
        await safe_answer(message, f"{E.INFO} Лидов пока нет. Начни с поиска!", reply_markup=main_menu_kb())
        return

    lines = [f"<b>{E.CHART} Статистика</b>\n"]

    # Источники
    lines.append(f"<b>Лидов всего:</b> {total}")
    lines.append(f"  • OSM-поиск: {stats['osm']}")
    lines.append(f"  • Chat Monitor: {stats['chat_monitor']}")

    # Статусы
    lines.append("\n<b>По статусам:</b>")
    for status, label in STATUS_LABELS.items():
        count = stats["statuses"].get(status, 0)
        if count:
            lines.append(f"  • {label}: {count}")

    # Конверсия: new → client
    clients = stats["statuses"].get("client", 0)
    if total > 0:
        conv = round(clients / total * 100, 1)
        lines.append(f"\n<b>Конверсия</b> (new→client): {conv}%")

    # AI-расходы
    limit = settings.llm_daily_limit
    if limit > 0:
        lines.append(f"\n<b>AI-вызовов сегодня:</b> {stats['llm_today']}/{limit}")
    else:
        lines.append(f"\n<b>AI-вызовов сегодня:</b> {stats['llm_today']} (лимит не задан)")

    # Напоминания
    lines.append(f"<b>Активных напоминаний:</b> {stats['pending_reminders']}")

    # Воронка (AD-2): поиск → сохранение → анализ → сообщение → статус
    async with session_factory() as session:
        funnel = await repo.get_funnel(session, message.from_user.id)
    if funnel["total"] > 0:
        lines.append("\n<b>Воронка:</b>")
        lines.append(f"  • Найдено: {funnel['total']}")
        lines.append(f"  • С контактами: {funnel['saved']} ({funnel['save_rate']}%)")
        lines.append(f"  • Проанализировано: {funnel['analyzed']} ({funnel['analyze_rate']}%)")
        lines.append(f"  • Продвинуто по статусу: {funnel['advanced']} ({funnel['advance_rate']}%)")
        lines.append(f"  • Общая конверсия: {funnel['overall_rate']}%")

    await safe_answer(message, "\n".join(lines), reply_markup=main_menu_kb())


@commands_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    await state.clear()
    prefix = "Действие отменено." if current_state else "Отменять нечего."
    await safe_answer(message, f"{E.CHECK} {prefix}\n\n{E.HOME} Главное меню:", reply_markup=main_menu_kb())


@commands_router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    """/menu — вернуться в главное меню из любой точки."""
    await state.clear()
    await safe_answer(message, f"{E.HOME} Главное меню:", reply_markup=main_menu_kb())


@commands_router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext) -> None:
    """/search — быстрый запуск поиска компаний (город)."""
    await state.clear()
    await state.set_state(SearchFSM.waiting_city)
    await safe_answer(
        message, f"{E.SEARCH} В каком городе ищем? Напиши название, например: Москва"
    )


@commands_router.message(Command("leads"))
async def cmd_leads(message: Message, state: FSMContext) -> None:
    """/leads — открыть список лидов."""
    await state.clear()
    await safe_answer(
        message, f"{E.PEOPLE} Мои лиды. Выбери фильтр по статусу:",
        reply_markup=leads_filter_kb(),
    )


@fallback_router.callback_query(F.data.regexp(r"^(spg|ssv|san):"))
async def stale_search_card_callback(callback: CallbackQuery) -> None:
    await callback.answer(STALE_SEARCH_CARD_TEXT, show_alert=True)


@fallback_router.message()
async def fallback_message(message: Message) -> None:
    await safe_answer(message, FALLBACK_TEXT, reply_markup=main_menu_kb())
