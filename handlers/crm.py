"""Этапы 5-6: CRM-лайт (лиды, статусы, заметки) и напоминания."""

import csv
import io
import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from db import repo
from db.base import session_factory
from db.models import STATUS_LABELS, is_valid_status, utcnow
from handlers.company import format_lead_card
from keyboards.main_menu import lead_card_kb, leads_filter_kb, reminder_kb, reminders_list_kb, statuses_kb
from states.fsm import NoteFSM, ReminderFSM
from utils.emoji_config import E, P
from utils.fsm_input import get_text_input
from utils.safe_send import safe_answer, safe_edit

logger = logging.getLogger(__name__)
router = Router(name="crm")

MSG_INVALID_STATUS = f"{P.CROSS} Такого статуса не существует. Выбери статус кнопкой из списка."
MSG_BAD_DATA = f"{P.CROSS} Некорректные данные. Попробуй ещё раз."
MSG_LEAD_NOT_FOUND = f"{P.CROSS} Лид не найден."
MSG_TOO_MANY_REMINDERS = f"{P.TIMER} Слишком много активных напоминаний. Удали лишние перед созданием нового."


def parse_custom_date(raw: str) -> datetime | None:
    """Парсит 'ДД.ММ.ГГГГ ЧЧ:ММ' или 'ДД.ММ.ГГГГ' (тогда 10:00). None — если невалидно."""
    raw = raw.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%d.%m.%Y":
                dt = dt.replace(hour=10, minute=0)
            return dt
        except ValueError:
            continue
    return None


# ---------- Экспорт CSV (с лимитом и подтверждением) ----------

MAX_EXPORT_ROWS = 1000  # Лимит для предотвращения OOM

# SEC-FIX (CSV formula injection): символы, с которых Excel/LibreOffice
# начинает интерпретировать ячейку как формулу.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:
    """Экранирует значение от CSV formula injection.

    Поля вида "=cmd|'/c calc'!A1" или "=HYPERLINK(...)" при открытии в Excel
    выполняются как формулы. Префикс-apostrophe заставляет Excel трактовать
    ячейку как текст.
    """
    s = "" if value is None else str(value)
    if s[:1] in _CSV_FORMULA_PREFIXES:
        return "'" + s
    return s


@router.callback_query(F.data == "leads:export")
async def export_leads_confirm(callback: CallbackQuery) -> None:
    """Показывает подтверждение перед экспортом (с счётчиком лидов).
    
    Сначала подсчитываем, сколько лидов будет экспортировано, и показываем
    юзеру inline-подтверждение. Только после нажатия "Да" — генерация CSV.
    """
    async with session_factory() as session:
        total = await repo.count_leads(session, callback.from_user.id)

    if total == 0:
        await callback.answer(f"{P.INFO} Нет лидов для экспорта.", show_alert=True)
        return

    # Если лидов больше MAX_EXPORT_ROWS — предупреждаем
    limit_note = f" (будет экспортировано только {MAX_EXPORT_ROWS} последних)" if total > MAX_EXPORT_ROWS else ""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"✅ Да, экспортировать {min(total, MAX_EXPORT_ROWS)} лидов{limit_note if total > MAX_EXPORT_ROWS else ''}",
                callback_data=f"expgo:{callback.from_user.id}",
            ),
            InlineKeyboardButton(text="Отмена", callback_data="menu:leads", icon_custom_emoji_id="5870657884844462243"),
        ],
    ])
    
    await safe_edit(
        callback.message,
        f"{E.LIST} Всего лидов: <b>{total}</b>{limit_note}.\n"
        "Файл будет содержать id, name, статус, phone, website, address, ai_score.\n"
        f"Продолжить экспорт?",
        reply_markup=kb,
    )
    # Показываем, что это не окончательный ответ — нужен confirm callback
    await callback.answer()


@router.callback_query(F.data.startswith("expgo:"))
async def export_leads_confirmed(callback: CallbackQuery) -> None:
    """Генерирует и отправляет CSV после подтверждения (с лимитом строк)."""
    # Простая валидация: expgo:<tg_user_id>. Юзер может нажать только свою кнопку,
    # но проверим дополнительно.
    try:
        exp_user_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return

    if exp_user_id != callback.from_user.id:
        await callback.answer("Нельзя экспортировать чужие лиды.", show_alert=True)
        return

    async with session_factory() as session:
        # Применяем лимит через последние обновлённые лиды
        leads = await repo.list_leads(
            session, callback.from_user.id, offset=0, limit=MAX_EXPORT_ROWS
        )
        if leads:
            await repo.log_action(session, callback.from_user.id, "export_csv", details=f"rows={len(leads)}")

    if not leads:
        await callback.answer(f"{P.INFO} Нет лидов для экспорта.", show_alert=True)
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "status", "source", "phone", "website", "address",
        "ai_score", "has_online_booking", "niche", "source_chat",
        "chat_username", "relevance_score", "note", "created_at", "updated_at",
    ])
    for lead in leads:
        writer.writerow([
            lead.id,
            _csv_safe(lead.name),
            _csv_safe(lead.status),
            _csv_safe(lead.source),
            _csv_safe(lead.phone),
            _csv_safe(lead.website),
            _csv_safe(lead.address),
            lead.ai_score if lead.ai_score is not None else "",
            "" if lead.has_online_booking is None else str(lead.has_online_booking).lower(),
            _csv_safe(lead.niche),
            _csv_safe(lead.source_chat),
            _csv_safe(lead.chat_username),
            f"{lead.relevance_score:.2f}" if lead.relevance_score is not None else "",
            _csv_safe(lead.note),
            lead.created_at.strftime("%Y-%m-%d %H:%M") if lead.created_at else "",
            lead.updated_at.strftime("%Y-%m-%d %H:%M") if lead.updated_at else "",
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM для корректного Excel
    filename = f"leads_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    await callback.message.answer_document(
        BufferedInputFile(csv_bytes, filename),
        caption=f"{E.LIST} Экспортировано лидов: {len(leads)}"
                + (f" (лимит {MAX_EXPORT_ROWS})" if len(leads) >= MAX_EXPORT_ROWS else ""),
    )
    await callback.answer()


# ---------- Удаление лида ----------

@router.callback_query(F.data.startswith("del:"))
async def delete_lead_confirm(callback: CallbackQuery) -> None:
    """Показывает подтверждение удаления лида."""
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да, удалить", callback_data=f"delyes:{lead_id}", icon_custom_emoji_id="5870633910337015697"),
            InlineKeyboardButton(text="Отмена", callback_data=f"lead:{lead_id}", icon_custom_emoji_id="5870657884844462243"),
        ],
    ])
    await safe_answer(
        callback.message,
        f"{E.WARNING} Удалить этот лид? Его можно будет восстановить кнопкой «Восстановить».",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delyes:"))
async def delete_lead_confirmed(callback: CallbackQuery) -> None:
    """Soft-delete лида после подтверждения. Предлагает кнопку восстановления."""
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    async with session_factory() as session:
        deleted = await repo.delete_lead(session, lead_id, callback.from_user.id)
        if deleted:
            await repo.log_action(session, callback.from_user.id, "lead_deleted", lead_id)
    if not deleted:
        await callback.answer(MSG_LEAD_NOT_FOUND, show_alert=True)
        return
    await safe_answer(
        callback.message,
        f"{E.CHECK} Лид удалён.\n"
        "Напоминания и данные сохранены — можно восстановить.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Восстановить", callback_data=f"restore:{lead_id}", icon_custom_emoji_id="6037496202990194718")],
            [InlineKeyboardButton(text="К лидам", callback_data="menu:leads", icon_custom_emoji_id="5870772616305839506")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("restore:"))
async def restore_lead_handler(callback: CallbackQuery) -> None:
    """Восстанавливает soft-deleted лид."""
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    async with session_factory() as session:
        lead = await repo.restore_lead(session, lead_id, callback.from_user.id)
        if lead is not None:
            await repo.log_action(session, callback.from_user.id, "lead_restored", lead_id)
    if lead is None:
        await callback.answer(MSG_LEAD_NOT_FOUND, show_alert=True)
        return
    await safe_answer(
        callback.message,
        f"{E.CHECK} Лид восстановлен.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="К карточке", callback_data=f"lead:{lead.id}", icon_custom_emoji_id="5870982283724328568")],
            [InlineKeyboardButton(text="К лидам", callback_data="menu:leads", icon_custom_emoji_id="5870772616305839506")],
        ]),
    )
    await callback.answer()


# ---------- Список лидов ----------

@router.callback_query(F.data == "menu:leads")
async def show_leads_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_answer(
        callback.message, f"{E.LIST} Мои лиды. Выбери фильтр по статусу:", reply_markup=leads_filter_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("leads:"))
async def list_leads_filtered(callback: CallbackQuery) -> None:
    raw = callback.data.split(":", 1)[1]

    # Экспорт обрабатывается отдельным хендлером выше (L-4: явная защита от порядка регистрации)
    if raw == "export":
        return

    # Поддержка пагинации: leads:all:1, leads:new:2, leads:no_booking:0 и т.д.
    parts = raw.rsplit(":", 1)
    raw_filter = parts[0]
    try:
        page = int(parts[1]) if len(parts) == 2 else 0
    except ValueError:
        page = 0

    page_size = repo.PAGE_SIZE
    offset = page * page_size

    # Разбираем фильтр в единый набор kwargs для repo (CODE-5: убрано дублирование)
    filter_kwargs: dict = {}
    if raw_filter == "no_booking":
        filter_kwargs["only_no_booking"] = True
        label = "Без онлайн-записи"
    elif raw_filter == "chat_monitor":
        filter_kwargs["source"] = "chat_monitor"
        label = "Chat Monitor"
    else:
        status = None if raw_filter == "all" else raw_filter
        if status is not None and not is_valid_status(status):
            await callback.answer(MSG_INVALID_STATUS, show_alert=True)
            return
        if status is not None:
            filter_kwargs["status"] = status
        label = "Все" if status is None else STATUS_LABELS[status]

    async with session_factory() as session:
        total = await repo.count_leads(session, callback.from_user.id, **filter_kwargs)
        leads = await repo.list_leads(
            session, callback.from_user.id, offset=offset, limit=page_size, **filter_kwargs
        )

    if not leads and page == 0:
        await safe_answer(
            callback.message,
            f"{E.INFO} По фильтру «{label}» лидов пока нет.\n"
            "Начни с поиска — «Новый поиск» в меню.",
        )
        await callback.answer()
        return

    rows = [
        [InlineKeyboardButton(
            text=f"{lead.name[:35]} · {STATUS_LABELS.get(lead.status, lead.status)}"
                 + (f" · {lead.ai_score}" if lead.ai_score is not None else ""),
            callback_data=f"lead:{lead.id}",
        )]
        for lead in leads
    ]

    # Навигация по страницам
    total_pages = max(1, (total + page_size - 1) // page_size)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="Назад", callback_data=f"leads:{raw_filter}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="Вперёд", callback_data=f"leads:{raw_filter}:{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Фильтры", callback_data="menu:leads", icon_custom_emoji_id="6037249452824072506")])
    await safe_answer(
        callback.message,
        f"{E.PEOPLE} Лиды ({label}): {total}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lead:"))
async def show_lead_card(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    async with session_factory() as session:
        lead = await repo.get_lead(session, lead_id, callback.from_user.id)
        reminders = await repo.list_reminders_for_lead(session, lead_id, callback.from_user.id)
    if lead is None:
        await callback.answer(MSG_LEAD_NOT_FOUND, show_alert=True)
        return
    # Считаем только неотправленные напоминания для счётчика на кнопке
    pending = [r for r in reminders if not r.is_sent]
    await safe_answer(
        callback.message,
        format_lead_card(lead),
        reply_markup=lead_card_kb(lead.id, has_analysis=bool(lead.ai_analysis), reminder_count=len(pending)),
    )
    await callback.answer()


# ---------- Статусы ----------

@router.callback_query(F.data.startswith("sts:"))
async def change_status(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    try:
        lead_id = int(parts[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    status = parts[2]
    # Явная проверка невалидного статуса (в т.ч. модифицированный callback)
    if not is_valid_status(status):
        await callback.answer(MSG_INVALID_STATUS, show_alert=True)
        return

    async with session_factory() as session:
        try:
            lead = await repo.set_lead_status(session, lead_id, callback.from_user.id, status)
        except ValueError:
            await callback.answer(MSG_INVALID_STATUS, show_alert=True)
            return
        if lead is not None:
            await repo.log_action(session, callback.from_user.id, "status_changed", lead_id, details=status)
    if lead is None:
        await callback.answer(MSG_LEAD_NOT_FOUND, show_alert=True)
        return
    await safe_edit(
        callback.message,
        format_lead_card(lead),
        reply_markup=lead_card_kb(lead.id, has_analysis=bool(lead.ai_analysis)),
    )
    await callback.answer(f"{P.CHECK} Статус: {STATUS_LABELS[status]}")


@router.callback_query(F.data.startswith("st:"))
async def status_menu(callback: CallbackQuery) -> None:
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=statuses_kb(lead_id))
    await callback.answer()


# ---------- Заметки ----------

@router.callback_query(F.data.startswith("note:"))
async def note_start(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    async with session_factory() as session:
        lead = await repo.get_lead(session, lead_id, callback.from_user.id)
    if lead is None:
        await callback.answer(MSG_LEAD_NOT_FOUND, show_alert=True)
        return
    current = f"Текущая заметка:\n<i>{escape(lead.note)}</i>\n\n" if lead.note else ""
    await state.set_state(NoteFSM.waiting_note)
    await state.update_data(note_lead_id=lead_id)
    await safe_answer(callback.message, f"{current}{E.NOTE} Пришли текст новой заметки:")
    await callback.answer()


@router.message(NoteFSM.waiting_note)
async def note_received(message: Message, state: FSMContext) -> None:
    # F3: фото/стикер раньше давали буквально «Пустая заметка...» без выхода — мучили юзера.
    text = await get_text_input(message, "Пустая заметка не сохранится. Пришли текст")
    if text is None:
        return
    if len(text) > 2000:
        await message.answer("Заметка слишком длинная (максимум 2000 символов). Сократи текст.\n\nИли пришли /cancel для отмены.")
        return
    data = await state.get_data()
    lead_id = data.get("note_lead_id")
    if lead_id is None:
        # FSM-данные утрачены (Redis TTL/рестарт) — не пишем «в никуда».
        logger.warning("note_received: note_lead_id потерян (state evicted)")
        await state.clear()
        await safe_answer(message, f"{E.CROSS} Не помню, к какому лиду шла заметка. Открой лид заново.")
        return
    async with session_factory() as session:
        lead = await repo.set_lead_note(session, lead_id, message.from_user.id, text)
        if lead is not None:
            await repo.log_action(session, message.from_user.id, "note_updated", lead_id)
    await state.clear()
    if lead is None:
        await safe_answer(message, f"{E.CROSS} Лид не найден.")
        return
    await safe_answer(
        message,
        format_lead_card(lead),
        reply_markup=lead_card_kb(lead.id, has_analysis=bool(lead.ai_analysis)),
    )


# ---------- Напоминания ----------

@router.callback_query(F.data.startswith("remd:"))
async def reminder_days(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    try:
        lead_id = int(parts[1])
        days = int(parts[2])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    if days not in (1, 3, 7, 14):
        await callback.answer(f"{P.CROSS} Некорректный интервал. Выбери кнопкой.", show_alert=True)
        return

    async with session_factory() as session:
        lead = await repo.get_lead(session, lead_id, callback.from_user.id)
        if lead is None:
            await callback.answer(MSG_LEAD_NOT_FOUND, show_alert=True)
            return
        remind_at = utcnow() + timedelta(days=days)
        reminder = await repo.create_reminder(
            session,
            lead_id,
            callback.from_user.id,
            remind_at,
            text=f"Пора связаться: {lead.name}",
            max_active=settings.max_active_reminders_per_user,
        )
        if reminder is None:
            await callback.answer(MSG_TOO_MANY_REMINDERS, show_alert=True)
            return
        await repo.log_action(session, callback.from_user.id, "reminder_created", lead_id, details=f"days={days}")
    await callback.answer(f"{P.CHECK} Напомню через {days} дн.", show_alert=False)


@router.callback_query(F.data.startswith("remc:"))
async def reminder_custom_start(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    await state.set_state(ReminderFSM.waiting_date)
    await state.update_data(reminder_lead_id=lead_id)
    await safe_answer(
        callback.message,
        f"{E.CALENDAR} Пришли дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b> "
        "(или только дату — напомню в 10:00).\nВремя — по UTC.",
    )
    await callback.answer()


@router.message(ReminderFSM.waiting_date)
async def reminder_custom_received(message: Message, state: FSMContext) -> None:
    # F3: фото/стикер раньше давали «Не понял дату...» без объяснения и выхода.
    text = await get_text_input(message, "Не понял дату. Формат: 25.12.2026 15:30 или 25.12.2026")
    if text is None:
        return
    dt = parse_custom_date(text)
    if dt is None:
        await message.answer("Не понял дату. Формат: 25.12.2026 15:30 или 25.12.2026\n\nИли пришли /cancel для отмены.")
        return
    if dt <= utcnow():
        await message.answer("Эта дата уже в прошлом. Пришли дату в будущем.\n\nИли пришли /cancel для отмены.")
        return
    data = await state.get_data()
    lead_id = data.get("reminder_lead_id")
    async with session_factory() as session:
        lead = await repo.get_lead(session, lead_id, message.from_user.id)
        if lead is None:
            await state.clear()
            await safe_answer(message, f"{E.CROSS} Лид не найден.")
            return
        reminder = await repo.create_reminder(
            session,
            lead_id,
            message.from_user.id,
            dt,
            text=f"Пора связаться: {lead.name}",
            max_active=settings.max_active_reminders_per_user,
        )
        if reminder is None:
            await state.clear()
            await safe_answer(message, MSG_TOO_MANY_REMINDERS)
            return
        await repo.log_action(session, message.from_user.id, "reminder_created", lead_id, details=dt.isoformat())
    await state.clear()
    await safe_answer(
        message, f"{E.TIMER} Напомню {dt.strftime('%d.%m.%Y %H:%M')} (UTC) {E.CHECK}"
    )


@router.callback_query(F.data.startswith("rem:"))
async def reminder_menu(callback: CallbackQuery) -> None:
    """Показывает список активных напоминаний + кнопки добавления нового."""
    try:
        lead_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    async with session_factory() as session:
        reminders = await repo.list_reminders_for_lead(session, lead_id, callback.from_user.id)
    pending = [r for r in reminders if not r.is_sent]
    if pending:
        lines = [f"{E.TIMER} Активные напоминания для этого лида:"]
        for r in pending:
            lines.append(f"• {r.remind_at.strftime('%d.%m.%Y %H:%M')} UTC — {r.text[:60] if r.text else '—'}")
        lines.append("\nНажми на дату чтобы удалить, или добавь новое:")
        await safe_answer(
            callback.message,
            "\n".join(lines),
            reply_markup=reminders_list_kb(lead_id, pending),
        )
    else:
        await safe_answer(
            callback.message,
            f"{E.TIMER} Когда напомнить об этом лиде?",
            reply_markup=reminder_kb(lead_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("remdel:"))
async def delete_reminder_handler(callback: CallbackQuery) -> None:
    """Удаляет напоминание по кнопке из списка."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    try:
        reminder_id = int(parts[1])
        lead_id = int(parts[2])
    except ValueError:
        await callback.answer(MSG_BAD_DATA, show_alert=True)
        return
    async with session_factory() as session:
        deleted = await repo.delete_reminder(session, reminder_id, callback.from_user.id)
        if deleted:
            await repo.log_action(session, callback.from_user.id, "reminder_deleted", lead_id)
    if not deleted:
        await callback.answer("Напоминание уже удалено.", show_alert=True)
        return
    # Обновляем список напоминаний
    async with session_factory() as session:
        reminders = await repo.list_reminders_for_lead(session, lead_id, callback.from_user.id)
    pending = [r for r in reminders if not r.is_sent]
    await callback.answer(f"{E.CHECK} Напоминание удалено.")
    if pending:
        lines = [f"{E.TIMER} Активные напоминания:"]
        for r in pending:
            lines.append(f"• {r.remind_at.strftime('%d.%m.%Y %H:%M')} UTC — {r.text[:60] if r.text else '—'}")
        lines.append("\nНажми на дату чтобы удалить, или добавь новое:")
        await safe_answer(
            callback.message,
            "\n".join(lines),
            reply_markup=reminders_list_kb(lead_id, pending),
        )
    else:
        await safe_answer(
            callback.message,
            f"{E.TIMER} Напоминаний нет. Когда напомнить об этом лиде?",
            reply_markup=reminder_kb(lead_id),
        )
