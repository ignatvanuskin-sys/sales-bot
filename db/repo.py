"""CRUD-операции. Все запросы к лидам/напоминаниям скоупятся по owner_tg_id."""

import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from chat_monitor.config_store import (
    DEFAULT_MIN_SCORE,
    ChatMonitorConfig,
    deserialize_chat_refs,
    serialize_chat_refs,
)
from db.models import AuditLog, ChatMessageInbox, ChatMonitorSettings, LLMCallLog, Lead, Reminder, User, is_valid_status, utcnow

logger = logging.getLogger(__name__)


# ---------- User ----------

async def get_user(session: AsyncSession, tg_user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
    return result.scalar_one_or_none()


async def get_or_create_user(session: AsyncSession, tg_user_id: int, username: str | None = None) -> User:
    user = await get_user(session, tg_user_id)
    if user is None:
        user = User(tg_user_id=tg_user_id, username=username)
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            # F6: конкурентный /start от того же нового пользователя — тот, кто
            # проиграл гонку уникального tg_user_id, брал 500. Делаем fallback:
            # откатываемся и читаем строку, созданную победителем гонки.
            await session.rollback()
            user = await get_user(session, tg_user_id)
            if user is None:  # pragma: no cover — параноидальный случай
                raise
        else:
            await session.refresh(user)
    elif username and user.username != username:
        user.username = username
        await session.commit()
    return user


# ---------- Chat Monitor Settings ----------

def chat_monitor_settings_to_config(settings: ChatMonitorSettings) -> ChatMonitorConfig:
    return ChatMonitorConfig(
        owner_tg_id=settings.owner_tg_id,
        is_enabled=bool(settings.is_enabled),
        chats=deserialize_chat_refs(settings.chats),
        min_score=float(settings.min_score),
    )


async def get_or_create_chat_monitor_settings(
    session: AsyncSession,
    owner_tg_id: int,
    default_chats: list[str] | None = None,
    default_min_score: float = DEFAULT_MIN_SCORE,
) -> ChatMonitorSettings:
    result = await session.execute(
        select(ChatMonitorSettings).where(ChatMonitorSettings.owner_tg_id == owner_tg_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ChatMonitorSettings(
            owner_tg_id=owner_tg_id,
            chats=serialize_chat_refs(default_chats or []),
            min_score=default_min_score,
        )
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def save_chat_monitor_settings(
    session: AsyncSession,
    owner_tg_id: int,
    chats: list[str],
    min_score: float,
    is_enabled: bool,
) -> ChatMonitorSettings:
    settings = await get_or_create_chat_monitor_settings(session, owner_tg_id)
    settings.chats = serialize_chat_refs(chats)
    settings.min_score = min_score
    settings.is_enabled = is_enabled
    settings.updated_at = utcnow()
    await session.commit()
    await session.refresh(settings)
    return settings


async def add_chat_monitor_chats(
    session: AsyncSession,
    owner_tg_id: int,
    chats_to_add: list[str],
    default_chats: list[str] | None = None,
    default_min_score: float = DEFAULT_MIN_SCORE,
    max_chats: int = 100,
) -> ChatMonitorSettings:
    settings = await get_or_create_chat_monitor_settings(
        session, owner_tg_id, default_chats, default_min_score
    )
    chats = deserialize_chat_refs(settings.chats)
    for chat in chats_to_add:
        if chat not in chats and len(chats) < max_chats:
            chats.append(chat)
    settings.chats = serialize_chat_refs(chats)
    settings.updated_at = utcnow()
    await session.commit()
    await session.refresh(settings)
    return settings


async def delete_chat_monitor_chat(
    session: AsyncSession,
    owner_tg_id: int,
    index: int,
) -> ChatMonitorSettings | None:
    settings = await get_or_create_chat_monitor_settings(session, owner_tg_id)
    chats = deserialize_chat_refs(settings.chats)
    if not 0 <= index < len(chats):
        return None
    del chats[index]
    settings.chats = serialize_chat_refs(chats)
    settings.updated_at = utcnow()
    await session.commit()
    await session.refresh(settings)
    return settings


async def set_chat_monitor_min_score(
    session: AsyncSession,
    owner_tg_id: int,
    min_score: float,
) -> ChatMonitorSettings:
    settings = await get_or_create_chat_monitor_settings(session, owner_tg_id)
    settings.min_score = min_score
    settings.updated_at = utcnow()
    await session.commit()
    await session.refresh(settings)
    return settings


async def toggle_chat_monitor_enabled(
    session: AsyncSession,
    owner_tg_id: int,
) -> ChatMonitorSettings:
    settings = await get_or_create_chat_monitor_settings(session, owner_tg_id)
    settings.is_enabled = not settings.is_enabled
    settings.updated_at = utcnow()
    await session.commit()
    await session.refresh(settings)
    return settings


# ---------- Lead ----------

async def create_lead(
    session: AsyncSession,
    owner_tg_id: int,
    name: str,
    address: str | None = None,
    phone: str | None = None,
    website: str | None = None,
    socials: str | None = None,
    source: str = "osm",
) -> Lead:
    """Создаёт лида (обычно OSM). При гонке дедупликации возвращает существующий.

    Между проверкой find_lead_by_name_address и вставкой другой процесс
    (chat_monitor / повторный клик по «Сохранить») мог успеть записать того же
    лида. Частичный UNIQUE-индекс uq_osm_lead_dedup отклоняет дубль ошибкой
    IntegrityError (gap-2): откатываем транзакцию и возвращаем уже
    существующий лид — для вызывающего кода это «лид уже существует».
    """
    lead = Lead(
        owner_tg_id=owner_tg_id,
        name=name,
        address=address,
        phone=phone,
        website=website,
        socials=socials,
        source=source,
    )
    session.add(lead)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await find_lead_by_name_address(session, owner_tg_id, name, address)
        if existing is None:
            # Конфликт вызван не дедупликацией — пробрасываем исходную ошибку
            raise
        return existing
    await session.refresh(lead)
    return lead


async def create_chat_lead(
    session: AsyncSession,
    owner_tg_id: int,
    source_chat: str,
    user_id: int,
    username: str | None,
    message_text: str,
    message_date: datetime,
    relevance_score: float,
    llm_reasoning: str,
    niche: str = "nail",
    message_id: int | None = None,
) -> Lead:
    """Сохраняет релевантное сообщение из Chat Lead Monitor в общую CRM.

    SEC-H2: message_id обязателен — без него дедупликация (uq_chat_lead_dedup +
    inbox) молча отключается (NULL != NULL в unique-индексах), что позволило бы
    повторной доставке расплодить дубли лидов.
    """
    if message_id is None:
        raise ValueError("create_chat_lead: message_id is required (dedup would be disabled)")
    normalized_username = username.lstrip("@") if username else None
    name = f"@{normalized_username}" if normalized_username else f"Telegram user {user_id}"
    lead = Lead(
        owner_tg_id=owner_tg_id,
        name=name,
        address=source_chat,
        website=f"https://t.me/{normalized_username}" if normalized_username else None,
        source="chat_monitor",
        ai_score=round(relevance_score * 100),
        ai_analysis=llm_reasoning,
        has_online_booking=False,
        niche=niche,
        source_chat=source_chat,
        chat_username=normalized_username,
        chat_user_id=user_id,
        chat_message_id=message_id,
        message_text=message_text,
        message_date=message_date,
        relevance_score=relevance_score,
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


async def find_chat_lead_by_message(
    session: AsyncSession,
    owner_tg_id: int,
    source_chat: str,
    user_id: int,
    message_id: int | None,
) -> Lead | None:
    """Дедупликация повторной обработки одного Telegram-сообщения.

    SEC-H2: message_id=None НЕ может быть дедуплицирован (unique-индексы пропускают NULL),
    поэтому такой поиск бессмысленен и опасен — фейлим явно.
    """
    if message_id is None:
        raise ValueError("find_chat_lead_by_message: message_id is required (cannot dedup NULL)")
    result = await session.execute(
        select(Lead).where(
            Lead.owner_tg_id == owner_tg_id,
            Lead.source == "chat_monitor",
            Lead.source_chat == source_chat,
            Lead.chat_user_id == user_id,
            Lead.chat_message_id == message_id,
        )
    )
    return result.scalar_one_or_none()


async def claim_chat_message(
    session: AsyncSession,
    owner_tg_id: int,
    source_chat: str,
    user_id: int,
    message_id: int | None,
) -> bool:
    """Атомарный inbox claim до LLM.

    SEC-H2: message_id=None нельзя надёжно dedup'ить — раньше молча пропускали
    (return True), что приводило к дублям. Теперь фейлим явно: вызывающий код
    обязан обеспечить message_id (см. processor.candidate_from_event).
    """
    if message_id is None:
        raise ValueError("claim_chat_message: message_id is required (cannot dedup NULL)")
    claim = ChatMessageInbox(
        owner_tg_id=owner_tg_id,
        source_chat=source_chat,
        chat_user_id=user_id,
        chat_message_id=message_id,
    )
    session.add(claim)
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def release_chat_message_claim(
    session: AsyncSession,
    owner_tg_id: int,
    source_chat: str,
    user_id: int,
    message_id: int | None,
) -> None:
    if message_id is None:
        return
    from sqlalchemy import delete

    await session.execute(delete(ChatMessageInbox).where(
        ChatMessageInbox.owner_tg_id == owner_tg_id,
        ChatMessageInbox.source_chat == source_chat,
        ChatMessageInbox.chat_user_id == user_id,
        ChatMessageInbox.chat_message_id == message_id,
    ))
    await session.commit()


async def find_lead_by_name_address(
    session: AsyncSession, owner_tg_id: int, name: str, address: str | None
) -> Lead | None:
    """Для дедупликации при повторном сохранении из поиска."""
    result = await session.execute(
        select(Lead).where(
            Lead.owner_tg_id == owner_tg_id,
            Lead.name == name,
            Lead.address == address,
        )
    )
    return result.scalars().first()


async def get_lead(session: AsyncSession, lead_id: int, owner_tg_id: int, include_deleted: bool = False) -> Lead | None:
    """Получает лид по ID. По умолчанию скрывает soft-deleted (P-8)."""
    query = select(Lead).where(Lead.id == lead_id, Lead.owner_tg_id == owner_tg_id)
    if not include_deleted:
        query = query.where(Lead.deleted_at.is_(None))
    result = await session.execute(query)
    return result.scalar_one_or_none()


PAGE_SIZE = 50  # Максимум лидов на одну страницу в UI


async def count_leads(
    session: AsyncSession,
    owner_tg_id: int,
    status: str | None = None,
    only_no_booking: bool = False,
    source: str | None = None,
    include_deleted: bool = False,
) -> int:
    """Количество лидов по фильтру без загрузки строк."""
    query = select(func.count()).select_from(Lead).where(Lead.owner_tg_id == owner_tg_id)
    if not include_deleted:
        query = query.where(Lead.deleted_at.is_(None))
    if status is not None:
        if not is_valid_status(status):
            raise ValueError(f"Invalid status filter: {status!r}")
        query = query.where(Lead.status == status)
    if only_no_booking:
        query = query.where(Lead.has_online_booking.is_(False))
    if source is not None:
        query = query.where(Lead.source == source)
    return (await session.execute(query)).scalar_one()


async def list_leads(
    session: AsyncSession,
    owner_tg_id: int,
    status: str | None = None,
    only_no_booking: bool = False,
    source: str | None = None,
    offset: int = 0,
    limit: int = PAGE_SIZE,
    include_deleted: bool = False,
) -> list[Lead]:
    # Фильтры строятся ДО offset/limit — корректный порядок для SQL (CODE-2)
    query = select(Lead).where(Lead.owner_tg_id == owner_tg_id)
    if not include_deleted:
        query = query.where(Lead.deleted_at.is_(None))
    if status is not None:
        if not is_valid_status(status):
            raise ValueError(f"Invalid status filter: {status!r}")
        query = query.where(Lead.status == status)
    if only_no_booking:
        # Только явное False. None (не анализировали/не определили) НЕ попадает —
        # это не «нет записи», а «неизвестно».
        query = query.where(Lead.has_online_booking.is_(False))
    if source is not None:
        query = query.where(Lead.source == source)
    query = query.order_by(Lead.updated_at.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def set_lead_status(
    session: AsyncSession, lead_id: int, owner_tg_id: int, status: str
) -> Lead | None:
    """Меняет статус. Невалидный статус -> ValueError (хендлер покажет user-friendly текст)."""
    if not is_valid_status(status):
        raise ValueError(f"Invalid status: {status!r}")
    lead = await get_lead(session, lead_id, owner_tg_id)
    if lead is None:
        return None
    lead.status = status
    lead.updated_at = utcnow()
    await session.commit()
    await session.refresh(lead)
    return lead


async def set_lead_note(
    session: AsyncSession, lead_id: int, owner_tg_id: int, note: str
) -> Lead | None:
    lead = await get_lead(session, lead_id, owner_tg_id)
    if lead is None:
        return None
    lead.note = note
    lead.updated_at = utcnow()
    await session.commit()
    await session.refresh(lead)
    return lead


async def save_lead_analysis(
    session: AsyncSession,
    lead_id: int,
    owner_tg_id: int,
    score: int,
    analysis: str,
    has_online_booking: bool | None = None,
) -> Lead | None:
    lead = await get_lead(session, lead_id, owner_tg_id)
    if lead is None:
        return None
    lead.ai_score = score
    lead.ai_analysis = analysis
    lead.has_online_booking = has_online_booking
    lead.updated_at = utcnow()
    await session.commit()
    await session.refresh(lead)
    return lead


# ---------- Reminder ----------

async def create_reminder(
    session: AsyncSession,
    lead_id: int,
    owner_tg_id: int,
    remind_at: datetime,
    text: str = "",
    max_active: int = 0,
) -> Reminder | None:
    """Создаёт напоминание; при max_active > 0 атомарно применяет anti-DoS лимит.

    SQLite использует BEGIN IMMEDIATE для сериализации read-then-write. На
    PostgreSQL параллельные вызовы одного owner дополнительно сериализуются
    advisory-lock'ом на время транзакции.
    """
    if max_active > 0:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            # Один transaction-scoped lock на owner_tg_id.
            await session.execute(select(func.pg_advisory_xact_lock(owner_tg_id)))
        active = (await session.execute(
            select(func.count()).select_from(Reminder).where(
                Reminder.owner_tg_id == owner_tg_id,
                Reminder.is_sent.is_(False),
            )
        )).scalar_one()
        if active >= max_active:
            await session.rollback()
            return None
    reminder = Reminder(
        lead_id=lead_id,
        owner_tg_id=owner_tg_id,
        remind_at=remind_at,
        text=text,
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return reminder


async def count_active_reminders(session: AsyncSession, owner_tg_id: int) -> int:
    """Количество неотправленных напоминаний пользователя (для anti-DoS лимита)."""
    result = await session.execute(
        select(func.count()).select_from(Reminder).where(
            Reminder.owner_tg_id == owner_tg_id,
            Reminder.is_sent.is_(False),
        )
    )
    return result.scalar_one()


async def get_due_reminders(session: AsyncSession, now: datetime | None = None) -> list[Reminder]:
    """Возвращает просроченные напоминания с предзагрузкой лида (устраняет N+1, CODE-3)."""
    now = now or utcnow()
    result = await session.execute(
        select(Reminder)
        .options(selectinload(Reminder.lead))
        .where(Reminder.remind_at <= now, Reminder.is_sent.is_(False))
    )
    return list(result.scalars().all())


async def claim_due_reminders(
    session: AsyncSession,
    now: datetime | None = None,
    limit: int = 100,
) -> list[Reminder]:
    """Атомарно claim'ит ограниченный batch due-напоминаний.

    PostgreSQL: FOR UPDATE SKIP LOCKED позволяет нескольким инстансам брать
    разные строки. SQLite: BEGIN IMMEDIATE сериализует транзакцию целиком.
    Claimed строки получают is_sent=True до сетевой отправки; при ошибке сервис
    возвращает конкретную строку в unsent.

    F5-ФИКС: напоминания по soft-deleted лидам НЕ отправляются — пользователь,
    удаливший лид, ожидает, что бот перестанет о нём напоминать.
    """
    now = now or utcnow()
    async with session.begin():
        query = (
            select(Reminder)
            .options(selectinload(Reminder.lead))
            .join(Lead, Lead.id == Reminder.lead_id)
            .where(
                Reminder.remind_at <= now,
                Reminder.is_sent.is_(False),
                Lead.deleted_at.is_(None),
            )
            .order_by(Reminder.remind_at, Reminder.id)
            .limit(max(1, limit))
        )
        if session.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        reminders = list((await session.execute(query)).scalars().all())
        for reminder in reminders:
            reminder.is_sent = True
    return reminders


async def _set_reminder_sent(session: AsyncSession, reminder_id: int, value: bool, owner_tg_id: int) -> None:
    """Один UPDATE вместо SELECT + UPDATE (CODE-4), со скоупом по владельцу (SEC-M1)."""
    await session.execute(
        update(Reminder)
        .where(Reminder.id == reminder_id, Reminder.owner_tg_id == owner_tg_id)
        .values(is_sent=value)
    )
    await session.commit()


async def mark_reminder_sent(session: AsyncSession, reminder_id: int, owner_tg_id: int) -> None:
    await _set_reminder_sent(session, reminder_id, True, owner_tg_id)


async def mark_reminder_unsent(session: AsyncSession, reminder_id: int, owner_tg_id: int) -> None:
    """Откат при сбое отправки — напоминание будет переотправлено поллером."""
    await _set_reminder_sent(session, reminder_id, False, owner_tg_id)


# ---------- LLM Call Logging (P-2) ----------

async def log_llm_call(
    session: AsyncSession,
    owner_tg_id: int | None = None,
    operation: str | None = None,
) -> None:
    """Записывает факт LLM-вызова в журнал."""
    session.add(LLMCallLog(owner_tg_id=owner_tg_id, operation=operation))
    await session.commit()


async def count_today_llm_calls(session: AsyncSession, owner_tg_id: int | None = None) -> int:
    """Количество LLM-вызовов за сегодня (UTC). Использует COUNT(*) — не загружает строки."""
    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    query = select(func.count()).select_from(LLMCallLog).where(LLMCallLog.created_at >= today_start)
    if owner_tg_id is not None:
        # NULL — legacy rows до migration 0005; учитываем их, чтобы deployment
        # не получил искусственный сброс бюджета в день миграции.
        query = query.where(
            (LLMCallLog.owner_tg_id == owner_tg_id) | LLMCallLog.owner_tg_id.is_(None)
        )
    result = await session.execute(query)
    return result.scalar_one()


async def check_llm_budget(
    session: AsyncSession,
    daily_limit: int = 0,
    owner_tg_id: int | None = None,
    operation: str | None = None,
) -> tuple[bool, int]:
    """Проверяет, не превышен ли дневной лимит LLM-вызовов.

    Возвращает (разрешено, текущее_количество_вызовов).
    Если daily_limit <= 0 — лимит отключён, всегда разрешено.
    Логирует вызов только если разрешено.

    Проверка счётчика и вставка выполняются в ОДНОЙ транзакции (gap-4).
    Иначе два параллельных вызова (бот + chat_monitor) на границе лимита оба
    видели count < limit и оба записывались, превышая бюджет.
    Движок стартует транзакции как BEGIN IMMEDIATE (db/base.py): RESERVED-
    блокировка берётся в начале транзакции, поэтому второй писатель ждёт
    первого (busy_timeout) и перечитывает уже актуальный count. Даже без
    BEGIN IMMEDIATE SQLite сериализует писателей на уровне файла БД — худший
    исход гонки был бы ошибкой блокировки у «проигравшего», а не превышением
    лимита. Вызывающий код обязан передавать сессию без открытой транзакции.
    """
    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    async with session.begin():
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            # Сериализует quota check одного owner на текущие UTC-сутки.
            day_key = int(today_start.strftime("%Y%m%d"))
            owner_key = int(owner_tg_id or 0)
            await session.execute(
                select(func.pg_advisory_xact_lock(owner_key, day_key))
            )
        query = select(func.count()).select_from(LLMCallLog).where(LLMCallLog.created_at >= today_start)
        if owner_tg_id is not None:
            query = query.where(
                (LLMCallLog.owner_tg_id == owner_tg_id) | LLMCallLog.owner_tg_id.is_(None)
            )
        count = (await session.execute(query)).scalar_one()
        if daily_limit > 0 and count >= daily_limit:
            return False, count
        session.add(LLMCallLog(owner_tg_id=owner_tg_id, operation=operation))
    return True, count + 1


# ---------- Delete Lead (P-6 / audit 11.14) ----------

async def delete_lead(session: AsyncSession, lead_id: int, owner_tg_id: int) -> bool:
    """Soft-delete: помечает лид как удалённый (P-8). True если удалён.

    Напоминания и связанные данные НЕ удаляются — их можно восстановить
    вместе с лидом через restore_lead. Каскадное удаление остаётся на случай
    полной очистки (CODE-1).
    """
    lead = await get_lead(session, lead_id, owner_tg_id, include_deleted=False)
    if lead is None:
        return False
    # SEC-FIX-4: при удалении лида ПДн (текст чужого сообщения) обезличиваем
    # сразу — soft-delete оставляет запись для восстановления, но ПДн стираем.
    lead.message_text = None
    lead.deleted_at = utcnow()
    lead.updated_at = utcnow()
    await session.commit()
    return True


async def restore_lead(session: AsyncSession, lead_id: int, owner_tg_id: int) -> Lead | None:
    """Восстанавливает soft-deleted лид."""
    lead = await get_lead(session, lead_id, owner_tg_id, include_deleted=True)
    if lead is None or lead.deleted_at is None:
        return None
    lead.deleted_at = None
    lead.updated_at = utcnow()
    await session.commit()
    await session.refresh(lead)
    return lead


async def list_reminders_for_lead(
    session: AsyncSession, lead_id: int, owner_tg_id: int
) -> list[Reminder]:
    """Список напоминаний для лида."""
    result = await session.execute(
        select(Reminder).where(
            Reminder.lead_id == lead_id,
            Reminder.owner_tg_id == owner_tg_id,
        ).order_by(Reminder.remind_at)
    )
    return list(result.scalars().all())


async def delete_reminder(
    session: AsyncSession, reminder_id: int, owner_tg_id: int
) -> bool:
    """Удаляет напоминание. True если удалено."""
    result = await session.execute(
        select(Reminder).where(
            Reminder.id == reminder_id,
            Reminder.owner_tg_id == owner_tg_id,
        )
    )
    reminder = result.scalar_one_or_none()
    if reminder is None:
        return False
    await session.delete(reminder)
    await session.commit()
    return True


# ---------- Stats (AD-1) ----------

async def get_stats(session: AsyncSession, owner_tg_id: int) -> dict:
    """Агрегированная статистика для команды /stats.

    Оптимизировано: 3 запроса вместо 5 (ARCH-6).
    Источники и статусы получаются через GROUP BY, а не отдельными COUNT.

    Лидовые агрегаты считаются только по активным лидам (deleted_at IS NULL),
    иначе цифры расходятся с get_funnel и списками CRM (gap-3).
    pending_reminders намеренно считается без фильтра по удалённым лидам:
    напоминания soft-deleted лидов физически сохраняются и реально сработают
    (get_due_reminders их не отсекает), поэтому это честный счётчик
    «ожидающих отправки».
    """
    # Запрос 1: количество активных лидов по статусам (один GROUP BY вместо N запросов)
    status_rows = (await session.execute(
        select(Lead.status, func.count())
        .where(Lead.owner_tg_id == owner_tg_id, Lead.deleted_at.is_(None))
        .group_by(Lead.status)
    )).all()
    statuses = {row[0]: row[1] for row in status_rows}
    total = sum(statuses.values())

    # Запрос 2: количество активных лидов по источникам (один GROUP BY)
    source_rows = (await session.execute(
        select(Lead.source, func.count())
        .where(Lead.owner_tg_id == owner_tg_id, Lead.deleted_at.is_(None))
        .group_by(Lead.source)
    )).all()
    sources = {row[0]: row[1] for row in source_rows}

    # Запрос 3: LLM-вызовы сегодня + активные напоминания (два лёгких COUNT)
    llm_today = await count_today_llm_calls(session, owner_tg_id)
    pending_reminders = (await session.execute(
        select(func.count()).select_from(Reminder).where(
            Reminder.owner_tg_id == owner_tg_id, Reminder.is_sent.is_(False)
        )
    )).scalar_one()

    return {
        "total": total,
        "osm": sources.get("osm", 0),
        "chat_monitor": sources.get("chat_monitor", 0),
        "statuses": statuses,
        "llm_today": llm_today,
        "pending_reminders": pending_reminders,
    }


async def get_funnel(session: AsyncSession, owner_tg_id: int) -> dict:
    """Воронка для /stats (AD-2): поиск → сохранение → анализ → сообщение → статус.

    Каждый шаг воронки:
      leads        — всего активных лидов (deleted_at IS NULL);
      saved        — лидов с контактом (phone или website);
      analyzed     — лидов с AI-анализом (ai_score IS NOT NULL);
      with_messages — лидов с сообщениями для контакта (статус != new);
      advanced     — лидов со статусом written/replied/client (исключая new и rejected).

    Возвращает словарь со счётчиками и конверсиями между шагами (в процентах).
    Один запрос COUNT(*) с CASE WHEN вместо 5 отдельных запросов.
    """
    from sqlalchemy import case

    row = (await session.execute(
        select(
            func.count().label("total"),
            func.sum(case((Lead.phone.is_not(None) | Lead.website.is_not(None), 1), else_=0)).label("saved"),
            func.sum(case((Lead.ai_score.is_not(None), 1), else_=0)).label("analyzed"),
            func.sum(case(
                (Lead.status.in_(["written", "replied", "client"]), 1),
                else_=0,
            )).label("advanced"),
        ).where(
            Lead.owner_tg_id == owner_tg_id,
            Lead.deleted_at.is_(None),
        )
    )).one()

    total = int(row.total or 0)
    saved = int(row.saved or 0)
    analyzed = int(row.analyzed or 0)
    advanced = int(row.advanced or 0)

    def pct(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator * 100, 1)

    return {
        "total": total,
        "saved": saved,
        "analyzed": analyzed,
        "advanced": advanced,
        "save_rate": pct(saved, total),
        "analyze_rate": pct(analyzed, saved),
        "advance_rate": pct(advanced, analyzed),
        "overall_rate": pct(advanced, total),
    }


# ---------- Audit Log (AUDIT-1) ----------

async def log_action(
    session: AsyncSession,
    owner_tg_id: int,
    action: str,
    lead_id: int | None = None,
    details: str | None = None,
) -> None:
    """Пишет действие пользователя в audit_log. Best-effort: сбой аудита
    не должен ломать бизнес-операцию — ошибка только логируется."""
    try:
        session.add(AuditLog(
            owner_tg_id=owner_tg_id,
            action=action,
            lead_id=lead_id,
            details=details,
        ))
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning(
            "audit_log write failed: action=%s owner=%s lead=%s",
            action, owner_tg_id, lead_id, exc_info=True,
        )
