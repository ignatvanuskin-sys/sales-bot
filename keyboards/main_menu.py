"""Inline-клавиатуры бота.

ВАЖНО: кастомные эмодзи (<tg-emoji>) в тексте кнопок НЕ рендерятся Telegram,
поэтому в кнопках их нет. Обычный юникод — только ✅/❌ для статуса,
остальные кнопки без эмодзи (эмодзи переносятся в текст сообщения).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import STATUS_LABELS
from services.places import CATEGORIES


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Новый поиск", callback_data="menu:search")],
            [InlineKeyboardButton(text="Мои лиды", callback_data="menu:leads")],
            [InlineKeyboardButton(text="Chat Monitor", callback_data="menu:chat_monitor")],
            [InlineKeyboardButton(text="Настройки", callback_data="menu:settings")],
        ]
    )


def chat_monitor_kb(is_enabled: bool, has_chats: bool) -> InlineKeyboardMarkup:
    toggle_text = "Выключить мониторинг" if is_enabled else "Включить мониторинг"
    rows = [
        [InlineKeyboardButton(text="Список чатов", callback_data="cm:chats")],
        [InlineKeyboardButton(text="Добавить чат", callback_data="cm:add")],
        [InlineKeyboardButton(text="Изменить threshold", callback_data="cm:threshold")],
        [InlineKeyboardButton(text=toggle_text, callback_data="cm:toggle")],
        [InlineKeyboardButton(text="Инструкция запуска", callback_data="cm:help")],
    ]
    if has_chats:
        rows.insert(2, [InlineKeyboardButton(text="Удалить чат", callback_data="cm:chats")])
    rows.append([InlineKeyboardButton(text="↩ В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_monitor_chats_kb(chats: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"Удалить {i + 1}", callback_data=f"cm:del:{i}")]
        for i, _chat in enumerate(chats)
    ]
    rows.append([InlineKeyboardButton(text="Добавить чат", callback_data="cm:add")])
    rows.append([InlineKeyboardButton(text="↩ Chat Monitor", callback_data="menu:chat_monitor")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def categories_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"cat:{slug}")]
        for slug, (label, _, _) in CATEGORIES.items()
    ]
    rows.append([InlineKeyboardButton(text="↩ В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


SEARCH_PAGE_SIZE = 5  # компактных результатов на один экран списка


def search_list_page_kb(
    page: int,
    total_pages: int,
    results: list[dict],
    offset: int,
    total: int,
) -> InlineKeyboardMarkup:
    """Компактный список результатов: одна кнопка = один результат (tap-to-open).

    Каждая кнопка подписана именем (обрезанным) и передаёт глобальный индекс
    результата через callback slo:<index>. Кнопки НЕ используют Unicode-эмодзи
    в text — иконки Premium задаются через icon_custom_emoji_id там, где уместно.
    """
    rows: list[list[InlineKeyboardButton]] = []
    page_slice = results[offset: offset + SEARCH_PAGE_SIZE]
    for i, company in enumerate(page_slice):
        idx = offset + i
        name = company.get("name", "—")
        # Обрезаем, чтобы кнопка была аккуратной (callback_data не содержит имя).
        label = name if len(name) <= 30 else name[:29] + "…"
        if idx == 0:
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"slo:{idx}", icon_custom_emoji_id="5870772616305839506",
            )])
        else:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"slo:{idx}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"slp:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"slp:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="↩ В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_card_kb(index: int, total: int, saved: bool, lead_id: int | None) -> InlineKeyboardMarkup:
    action_row = []
    if saved and lead_id is not None:
        action_row.append(InlineKeyboardButton(text="В лидах", callback_data=f"lead:{lead_id}",
                                               icon_custom_emoji_id="5870633910337015697"))
        action_row.append(InlineKeyboardButton(text="Анализ", callback_data=f"san:{index}",
                                               icon_custom_emoji_id="5870930636742595124"))
    else:
        action_row.append(InlineKeyboardButton(text="Сохранить в лиды", callback_data=f"ssv:{index}",
                                               icon_custom_emoji_id="5870676941614354370"))
        action_row.append(InlineKeyboardButton(text="Анализ", callback_data=f"san:{index}",
                                               icon_custom_emoji_id="5870930636742595124"))

    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton(text="Назад", callback_data=f"spg:{index - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data="noop"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton(text="Вперёд", callback_data=f"spg:{index + 1}"))

    back_row = []
    # «Назад к результатам» — возврат в компактный список (если пришли из него).
    # Используем callback slbk; обработчик в search.py отрисует список на текущей странице.
    back_row.append(InlineKeyboardButton(
        text="К результатам", callback_data="slbk", icon_custom_emoji_id="5893057118545646106",
    ))

    return InlineKeyboardMarkup(
        inline_keyboard=[
            action_row,
            nav_row,
            back_row,
            [InlineKeyboardButton(text="Меню", callback_data="menu:main")],
        ]
    )


def leads_filter_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Все", callback_data="leads:all")]]
    row: list[InlineKeyboardButton] = []
    for status, label in STATUS_LABELS.items():
        row.append(InlineKeyboardButton(text=label, callback_data=f"leads:{status}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Без онлайн-записи", callback_data="leads:no_booking")])
    rows.append([InlineKeyboardButton(text="Chat Monitor", callback_data="leads:chat_monitor")])
    rows.append([InlineKeyboardButton(text="Экспорт CSV", callback_data="leads:export")])
    rows.append([InlineKeyboardButton(text="↩ В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lead_card_kb(lead_id: int, has_analysis: bool, reminder_count: int = 0) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Статус", callback_data=f"st:{lead_id}"),
            InlineKeyboardButton(text="Заметка", callback_data=f"note:{lead_id}"),
        ],
        [InlineKeyboardButton(text="Анализ", callback_data=f"anl:{lead_id}")],
    ]
    if has_analysis:
        rows.append([InlineKeyboardButton(text="Сообщения для контакта", callback_data=f"gen:{lead_id}")])
    # Кнопка напоминаний: показывает счётчик если есть активные
    rem_label = f"Напомнить ({reminder_count})" if reminder_count > 0 else "Напомнить"
    rows.append([InlineKeyboardButton(text=rem_label, callback_data=f"rem:{lead_id}")])
    rows.append([InlineKeyboardButton(text="Удалить лид", callback_data=f"del:{lead_id}")])
    rows.append([InlineKeyboardButton(text="↩ К лидам", callback_data="menu:leads")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reminders_list_kb(lead_id: int, reminders: list) -> InlineKeyboardMarkup:
    """Клавиатура списка напоминаний с кнопками удаления каждого."""
    rows = []
    for r in reminders:
        date_str = r.remind_at.strftime("%d.%m.%Y %H:%M")
        rows.append([
            InlineKeyboardButton(
                text=f"🗑 {date_str} UTC",
                callback_data=f"remdel:{r.id}:{lead_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="+ Добавить напоминание", callback_data=f"rem:{lead_id}")])
    rows.append([InlineKeyboardButton(text="↩ К лиду", callback_data=f"lead:{lead_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def statuses_kb(lead_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"sts:{lead_id}:{status}")]
        for status, label in STATUS_LABELS.items()
    ]
    rows.append([InlineKeyboardButton(text="↩ Назад", callback_data=f"lead:{lead_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reminder_kb(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 день", callback_data=f"remd:{lead_id}:1"),
                InlineKeyboardButton(text="3 дня", callback_data=f"remd:{lead_id}:3"),
            ],
            [
                InlineKeyboardButton(text="7 дней", callback_data=f"remd:{lead_id}:7"),
                InlineKeyboardButton(text="14 дней", callback_data=f"remd:{lead_id}:14"),
            ],
            [InlineKeyboardButton(text="Своя дата", callback_data=f"remc:{lead_id}")],
            [InlineKeyboardButton(text="↩ Назад", callback_data=f"lead:{lead_id}")],
        ]
    )


def messages_kb(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Изменить короткое", callback_data=f"edm:{lead_id}:short"),
                InlineKeyboardButton(text="Изменить развёрнутое", callback_data=f"edm:{lead_id}:long"),
            ],
            [InlineKeyboardButton(text="↩ К лиду", callback_data=f"lead:{lead_id}")],
        ]
    )
