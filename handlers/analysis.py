"""Этап 3: AI-анализ компании по кнопке (из поиска и из карточки лида)."""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import settings
from db import repo
from db.base import session_factory
from handlers.company import format_lead_card
from handlers.search import _ensure_lead_saved
from keyboards.main_menu import lead_card_kb
from services.ai import AIError, AIOverloadError, AIRateLimitError, analyze_company
from services import llm_cache
from states.fsm import SearchFSM
from utils.emoji_config import E, P
from utils.idempotency import IdempotencyLock
from utils.safe_send import safe_answer, safe_edit

logger = logging.getLogger(__name__)
router = Router(name="analysis")

MSG_RATE_LIMIT = f"{E.TIMER} Слишком много запросов к AI. Попробуй через минуту."
MSG_OVERLOADED = f"{E.TIMER} Бесплатная модель сейчас перегружена. Попробуй через минуту."
MSG_AI_FAILED = f"{E.CROSS} Не получилось выполнить анализ. Попробуй ещё раз чуть позже."
MSG_BUDGET = f"{E.TIMER} Достигнут дневной лимит AI-вызовов. Попробуй завтра."
MSG_ALREADY_ANALYZING = f"{E.TIMER} Анализ для этого лида уже выполняется. Подожди завершения."

# B1: TTL idempotency-лока должен покрывать ХУДШЕЕ время выполнения LLM-запроса
# (timeout на попытку × набор ретраев + запас на задержки между попытками).
# Раньше было хардкод 120с — меньше реального worst-case (60с × (1+2 retries)),
# из-за чего повторный клик на T+121с запускал ВТОРОЙ платный анализ того же лида.
def _analysis_lock_ttl() -> int:
    worst_case = settings.llm_timeout_seconds * (1 + max(0, settings.llm_retry_attempts))
    # Запас 20% и минимум 60с, чтобы не держать лок слишком коротким/длинным.
    return max(60, int(worst_case * 1.2))


async def _run_analysis(owner_tg_id: int, lead_id: int) -> tuple[bool, str]:
    """Выполняет анализ и сохраняет результат. Возвращает (успех, текст ошибки для юзера).

    SECURITY-7: идемпотентность через Redis/memory lock — если для (owner, lead)
    уже идёт анализ, не запускаем второй. Работает в multi-instance окружении.
    B1: TTL лока привязан к фактическому бюджету времени LLM-запроса.
    """
    async with IdempotencyLock("analysis", owner_tg_id, lead_id, ttl=_analysis_lock_ttl()) as acquired:
        if not acquired:
            return False, MSG_ALREADY_ANALYZING

        async with session_factory() as session:
            lead = await repo.get_lead(session, lead_id, owner_tg_id)
        if lead is None:
            return False, f"{E.CROSS} Лид не найден."

        # PERF-2: Проверка кэша ПЕРЕД бюджетом (кэш-попадание не тратит вызовы)
        cache_key = llm_cache.make_cache_key(lead.name, lead.address, lead.phone, lead.website)
        cached = await llm_cache.get_cached_analysis(cache_key)
        if cached:
            score, analysis, has_online_booking = cached
            async with session_factory() as session:
                await repo.save_lead_analysis(
                    session, lead_id, owner_tg_id, score, analysis, has_online_booking
                )
                await repo.log_action(session, owner_tg_id, "analysis_run", lead_id, details="cached")
            return True, ""

        # P-2: Проверка дневного лимита LLM-вызовов (только для реальных вызовов)
        async with session_factory() as session:
            allowed, _count = await repo.check_llm_budget(
                session, settings.llm_daily_limit, owner_tg_id, "analysis"
            )
        if not allowed:
            return False, MSG_BUDGET

        try:
            score, analysis, has_online_booking = await analyze_company(
                name=lead.name, address=lead.address, phone=lead.phone, website=lead.website
            )
        except AIRateLimitError:
            return False, MSG_RATE_LIMIT
        except AIOverloadError:
            return False, MSG_OVERLOADED
        except AIError as exc:
            logger.error("AI analysis failed for lead=%s: %s", lead_id, exc)
            return False, MSG_AI_FAILED

        # Кэшируем успешный результат
        await llm_cache.set_cached_analysis(cache_key, score, analysis, has_online_booking)

        async with session_factory() as session:
            await repo.save_lead_analysis(
                session, lead_id, owner_tg_id, score, analysis, has_online_booking
            )
            await repo.log_action(session, owner_tg_id, "analysis_run", lead_id)
        return True, ""


async def _show_lead_after_analysis(callback: CallbackQuery, lead_id: int) -> None:
    async with session_factory() as session:
        lead = await repo.get_lead(session, lead_id, callback.from_user.id)
    if lead is None:
        await safe_answer(callback.message, f"{E.CROSS} Лид не найден.")
        return
    await safe_answer(
        callback.message,
        format_lead_card(lead),
        reply_markup=lead_card_kb(lead.id, has_analysis=bool(lead.ai_analysis)),
    )


async def _finish_status_message(status, callback: CallbackQuery, lead_id: int) -> None:
    """F4: удаляем промежуточное «Анализирую…» максимально безопасно.

    Раньше status.delete() без защиты: TelegramForbiddenError (юзер заблокировал
    бота) или BadRequest 'message to delete not found' улетали в
    ErrorHandlerMiddleware, ПОСЛЕ того как анализ уже сохранён в БД —
    пользователь получал ложное «Произошла ошибка».
    Здесь: не смогли удалить — молча продолжаем; показываем карточку результата.
    """
    try:
        await status.delete()
    except TelegramAPIError as exc:
        logger.debug("Could not delete 'analyzing' status message: %s", exc)
    await _show_lead_after_analysis(callback, lead_id)


@router.callback_query(SearchFSM.browsing, F.data.startswith("san:"))
async def analyze_from_search(callback: CallbackQuery, state: FSMContext) -> None:
    """Анализ из карточки поиска: сначала автоматически сохраняем в лиды."""
    try:
        index = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(f"{P.CROSS} Некорректные данные. Попробуй ещё раз.", show_alert=True)
        return

    lead_id = await _ensure_lead_saved(callback.from_user.id, state, index)
    if lead_id is None:
        await callback.answer(
            f"{P.RELOAD} Результаты устарели. Начни новый поиск.", show_alert=True
        )
        return

    await callback.answer()
    status = await safe_answer(callback.message, f"{E.CHART} Анализирую компанию…")
    ok, err = await _run_analysis(callback.from_user.id, lead_id)
    if not ok:
        await safe_edit(status, err)
        return
    await _finish_status_message(status, callback, lead_id)


@router.callback_query(F.data.startswith("anl:"))
async def analyze_from_lead(callback: CallbackQuery) -> None:
    """Анализ (или повторный анализ) из карточки лида."""
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(f"{P.CROSS} Некорректные данные. Попробуй ещё раз.", show_alert=True)
        return

    await callback.answer()
    status = await safe_answer(callback.message, f"{E.CHART} Анализирую компанию…")
    ok, err = await _run_analysis(callback.from_user.id, lead_id)
    if not ok:
        await safe_edit(status, err)
        return
    await _finish_status_message(status, callback, lead_id)
