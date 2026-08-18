"""Этап 2: FSM-поиск компаний (город -> категория -> карточки с пагинацией)."""

import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import IntegrityError

from db import repo
from db.base import session_factory
from handlers.company import format_company_card
from keyboards.main_menu import categories_kb, search_card_kb
from services.places import CATEGORIES, PlacesError, search_companies
from states.fsm import SearchFSM
from utils.emoji_config import E, P
from utils.fsm_input import get_text_input
from utils.safe_send import safe_answer, safe_edit

logger = logging.getLogger(__name__)
router = Router(name="search")


@router.callback_query(F.data == "menu:search")
async def start_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SearchFSM.waiting_city)
    await safe_answer(
        callback.message, f"{E.LOCATION} В каком городе ищем? Напиши название, например: Москва"
    )
    await callback.answer()


@router.message(SearchFSM.waiting_city)
async def city_received(message: Message, state: FSMContext) -> None:
    # F3: нетекстовый ввод (фото/стикер) раньше давал «Не понял город» и молчал —
    # пользователь не понимал причину и не знал про /cancel.
    city = await get_text_input(message, "Не понял город. Напиши текстом, например: Казань")
    if city is None:
        return
    if len(city) > 100:
        await message.answer("Слишком длинное название города. Напиши короче (до 100 символов).\n\nИли пришли /cancel для отмены.")
        return
    await state.update_data(city=city)
    await state.set_state(SearchFSM.waiting_category)
    await safe_answer(
        message,
        f"{E.CHECK} Город: <b>{escape(city)}</b>. Теперь выбери категорию бизнеса:",
        reply_markup=categories_kb(),
    )


@router.callback_query(SearchFSM.waiting_category, F.data.startswith("cat:"))
async def category_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    slug = callback.data.split(":", 1)[1]
    if slug not in CATEGORIES:
        await callback.answer(f"{P.CROSS} Неизвестная категория. Выбери кнопкой.", show_alert=True)
        return
    data = await state.get_data()
    city = data.get("city", "")
    label = CATEGORIES[slug][0]

    await callback.answer()
    status_msg = await safe_answer(
        callback.message, f"{E.SEARCH} Ищу «{label}» в городе {escape(city)}…"
    )

    try:
        companies = await search_companies(city, slug)
    except PlacesError:
        await safe_edit(
            status_msg,
            f"{E.CROSS} Сервис поиска сейчас недоступен. Попробуй ещё раз через пару минут.",
        )
        await state.clear()
        return

    if not companies:
        await safe_edit(
            status_msg,
            f"{E.INFO} По запросу «{label}» в городе {escape(city)} ничего не нашлось.\n"
            "Проверь написание города или попробуй другую категорию.",
        )
        await state.clear()
        return

    results = [c.to_dict() for c in companies]
    # saved_leads: index -> lead_id (какие результаты уже сохранены)
    await state.set_state(SearchFSM.browsing)
    await state.update_data(results=results, saved_leads={})

    await safe_edit(status_msg, f"{E.CHECK} Нашёл {len(results)} компаний. Листай карточки:")
    await _show_card(callback.message, state, 0, edit=False)


async def _show_card(message: Message, state: FSMContext, index: int, edit: bool = True) -> None:
    data = await state.get_data()
    results = data.get("results") or []
    if not results or not (0 <= index < len(results)):
        await safe_answer(message, f"{E.RELOAD} Результаты поиска устарели. Начни новый поиск.")
        return
    saved = data.get("saved_leads") or {}
    lead_id = saved.get(str(index))
    text = format_company_card(results[index], city=data.get("city"))
    kb = search_card_kb(index, len(results), saved=lead_id is not None, lead_id=lead_id)
    if edit:
        await safe_edit(message, text, reply_markup=kb)
    else:
        await safe_answer(message, text, reply_markup=kb)


@router.callback_query(SearchFSM.browsing, F.data.startswith("spg:"))
async def paginate(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        index = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(f"{P.CROSS} Некорректные данные. Попробуй ещё раз.", show_alert=True)
        return
    await _show_card(callback.message, state, index)
    await callback.answer()


@router.callback_query(SearchFSM.browsing, F.data.startswith("ssv:"))
async def save_from_search(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        index = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(f"{P.CROSS} Некорректные данные. Попробуй ещё раз.", show_alert=True)
        return

    data = await state.get_data()
    results = data.get("results") or []
    if not (0 <= index < len(results)):
        await callback.answer(
            f"{P.RELOAD} Результаты устарели. Начни новый поиск.", show_alert=True
        )
        return

    lead_id = await _ensure_lead_saved(callback.from_user.id, state, index)
    await _show_card(callback.message, state, index)
    await callback.answer(f"{P.CHECK} Сохранено в лиды" if lead_id else "Уже в лидах")


async def _ensure_lead_saved(owner_tg_id: int, state: FSMContext, index: int) -> int | None:
    """Сохраняет компанию из результатов как лида (с дедупликацией). Возвращает lead_id."""
    data = await state.get_data()
    results = data.get("results") or []
    saved = dict(data.get("saved_leads") or {})
    if str(index) in saved:
        return saved[str(index)]
    if not (0 <= index < len(results)):
        return None
    company = results[index]
    async with session_factory() as session:
        existing = await repo.find_lead_by_name_address(
            session, owner_tg_id, company["name"], company.get("address")
        )
        if existing is not None:
            lead = existing
        else:
            try:
                lead = await repo.create_lead(
                    session,
                    owner_tg_id=owner_tg_id,
                    name=company["name"],
                    address=company.get("address"),
                    phone=company.get("phone"),
                    website=company.get("website"),
                )
                await repo.log_action(session, owner_tg_id, "lead_saved", lead.id, details="from_search")
            except IntegrityError:
                # Гонка дедупликации (gap-2): другой процесс (chat_monitor /
                # повторный клик) успел вставить того же лида между проверкой и
                # вставкой, UNIQUE-индекс uq_osm_lead_dedup отклонил дубль.
                # repo.create_lead обрабатывает это сам — здесь страховка на
                # случай, если ошибка вырвалась наружу (например, на refresh).
                await session.rollback()
                lead = await repo.find_lead_by_name_address(
                    session, owner_tg_id, company["name"], company.get("address")
                )
                if lead is None:
                    # Конфликт не связан с дедупликацией — пробрасываем выше
                    raise
    saved[str(index)] = lead.id
    await state.update_data(saved_leads=saved)
    return lead.id
