"""Обработка сообщений Chat Lead Monitor без активных действий в Telegram.

Модуль работает только с events.NewMessage и не получает список участников чата.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import settings
from chat_monitor.filtering import author_on_cooldown, find_keywords
from chat_monitor.keywords_nail import KEYWORDS as _DEFAULT_KEYWORDS
from db import repo
from db.models import Lead, utcnow
from services.ai import score_nail_chat_message
from utils.bot_api import send_bot_message


def _get_keywords() -> tuple[str, ...]:
    """Возвращает ключевые слова из конфига или встроенные (ARCH-2)."""
    custom = settings.chat_monitor_keywords_list
    return custom if custom is not None else _DEFAULT_KEYWORDS

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatMessageCandidate:
    source_chat: str
    user_id: int
    username: str | None
    message_text: str
    message_date: datetime
    message_id: int | None = None
    niche: str = "nail"


def _naive_utc(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _analysis_text(reasoning: str, is_solo_master: bool, matched_keywords: list[str]) -> str:
    solo = "да" if is_solo_master else "нет"
    keywords = ", ".join(matched_keywords) if matched_keywords else "нет"
    return (
        f"Chat Lead Monitor: {reasoning}\n"
        f"Соло-мастер: {solo}\n"
        f"Ключевые слова: {keywords}"
    )


async def process_candidate(
    candidate: ChatMessageCandidate,
    session_factory: async_sessionmaker[AsyncSession],
    owner_tg_id: int,
    min_score: float,
    llm_client=None,
    keywords: tuple[str, ...] | None = None,
) -> Lead | None:
    """Фильтрует, скорит и сохраняет релевантное сообщение как Lead.

    keywords=None → берёт из конфига (CHAT_MONITOR_KEYWORDS) или встроенные (ARCH-2).
    """
    if owner_tg_id <= 0:
        raise ValueError("CHAT_MONITOR_OWNER_TG_ID must be set")

    effective_keywords = keywords if keywords is not None else _get_keywords()
    matched = find_keywords(candidate.message_text, effective_keywords)
    if not matched:
        return None

    # Атомарный claim ДО budget/LLM: два monitor-инстанса не скорят одно
    # Telegram-сообщение дважды.
    async with session_factory() as session:
        claimed = await repo.claim_chat_message(
            session,
            owner_tg_id,
            candidate.source_chat,
            candidate.user_id,
            candidate.message_id,
        )
    if not claimed:
        return None

    # CHATMON-1: Cooldown на автора — после скоринга его сообщения пропускаем
    # в течение cooldown, чтобы флуд одного автора не съедал LLM-бюджет.
    # Проверка ДО бюджета/LLM: пропуск не тратит ни дневной лимит, ни токены.
    if author_on_cooldown(candidate.user_id, settings.chat_monitor_author_cooldown_seconds):
        logger.info(
            "Chat lead skipped by author cooldown: chat=%s user=%s",
            candidate.source_chat, candidate.user_id,
        )
        return None

    # P-2: Проверка дневного лимита LLM-вызовов
    async with session_factory() as session:
        allowed, count = await repo.check_llm_budget(
            session, settings.llm_daily_limit, owner_tg_id, "chat_score"
        )
    if not allowed:
        logger.warning(
            "LLM daily limit reached (%d/%d), skipping chat message scoring: chat=%s user=%s",
            count, settings.llm_daily_limit, candidate.source_chat, candidate.user_id,
        )
        async with session_factory() as session:
            await repo.release_chat_message_claim(
                session,
                owner_tg_id,
                candidate.source_chat,
                candidate.user_id,
                candidate.message_id,
            )
        return None

    try:
        score, reasoning, is_solo_master = await score_nail_chat_message(
            candidate.message_text,
            username=candidate.username,
            source_chat=candidate.source_chat,
            client=llm_client,
        )
    except Exception:
        # Временный сбой LLM не должен навсегда блокировать retry сообщения.
        async with session_factory() as session:
            await repo.release_chat_message_claim(
                session,
                owner_tg_id,
                candidate.source_chat,
                candidate.user_id,
                candidate.message_id,
            )
        raise
    if score < min_score:
        logger.info(
            "Chat lead skipped by score: score=%.3f min=%.3f chat=%s user=%s",
            score,
            min_score,
            candidate.source_chat,
            candidate.user_id,
        )
        return None

    analysis = _analysis_text(reasoning, is_solo_master, matched)
    async with session_factory() as session:
        existing = await repo.find_chat_lead_by_message(
            session,
            owner_tg_id,
            candidate.source_chat,
            candidate.user_id,
            candidate.message_id,
        )
        if existing is not None:
            return existing
        lead = await repo.create_chat_lead(
            session,
            owner_tg_id=owner_tg_id,
            source_chat=candidate.source_chat,
            user_id=candidate.user_id,
            username=candidate.username,
            message_text=candidate.message_text,
            message_date=_naive_utc(candidate.message_date),
            relevance_score=score,
            llm_reasoning=analysis,
            niche=candidate.niche,
            message_id=candidate.message_id,
        )

    # Realtime-уведомление о новом чат-лиде
    if settings.bot_token and settings.chat_monitor_owner_tg_id:
        try:
            await _notify_new_lead(lead, settings.chat_monitor_owner_tg_id)
        except Exception:
            logger.debug("Failed to send new lead notification", exc_info=True)

    return lead


async def _notify_new_lead(lead: Lead, owner_tg_id: int) -> None:
    """Отправляет уведомление о новом чат-лиде через Bot API."""
    text = (
        "🆕 Новый лид из чата!\n\n"
        f"<b>{escape(lead.name)}</b>\n"
        f"Чат: {escape(lead.source_chat or '')}\n"
        f"Релевантность: {lead.relevance_score:.2f}\n"
    )
    if lead.message_text:
        escaped = escape(lead.message_text[:200])
        text += f"\n<i>{escaped}</i>"
    await send_bot_message(settings.bot_token, owner_tg_id, text)


async def candidate_from_event(event, niche: str = "nail") -> ChatMessageCandidate | None:
    """Извлекает минимум данных из Telethon NewMessage event."""
    message = getattr(event, "message", None)
    text = (getattr(event, "raw_text", None) or getattr(message, "message", None) or "").strip()
    if not text:
        return None

    # SEC-H2: без message_id дедупликация (inbox + uq_chat_lead_dedup) молча отключается
    # (NULL != NULL в unique-индексах) -> повторная доставка создаёт дубли лидов.
    # Безопаснее пропустить такое событие, чем расплодить дубли и спам-уведомления.
    message_id = getattr(message, "id", None)
    if message_id is None:
        return None

    sender = await event.get_sender()
    user_id = getattr(sender, "id", None) or getattr(event, "sender_id", None)
    if user_id is None:
        return None

    chat = await event.get_chat()
    chat_id = getattr(event, "chat_id", None)
    chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "unknown_chat"
    source_chat = f"{chat_title} ({chat_id})" if chat_id is not None else str(chat_title)

    return ChatMessageCandidate(
        source_chat=source_chat,
        user_id=int(user_id),
        username=getattr(sender, "username", None),
        message_text=text,
        message_date=_naive_utc(getattr(message, "date", None)),
        message_id=message_id,
        niche=niche,
    )


async def process_event(
    event,
    session_factory: async_sessionmaker[AsyncSession],
    owner_tg_id: int,
    min_score: float,
    llm_client=None,
) -> Lead | None:
    candidate = await candidate_from_event(event)
    if candidate is None:
        return None
    return await process_candidate(candidate, session_factory, owner_tg_id, min_score, llm_client)
