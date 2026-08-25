"""Форматирование карточек компании/лида. Пустые поля не показываем вообще.

ВАЖНО: escape() применяется только к пользовательским/внешним данным
(название, адрес, телефон и т.д.), но НЕ к HTML-разметке эмодзи (E.*),
которую мы вставляем сами, — иначе <tg-emoji> превратится в сырой текст.
"""

from html import escape
from urllib.parse import quote

from config import settings
from db.models import STATUS_LABELS, Lead
from utils.emoji_config import E

# Домен 2ГИС берётся из конфига (ARCH-3).
# Дефолт: 2gis.kz (Казахстан). Для РФ: GIS_DOMAIN=2gis.ru в .env.
def _gis_domain() -> str:
    return settings.gis_domain

# Порог «высокого» общего скора для строки «насколько нужен мой сервис».
# Ниже — бизнес выглядит слабым (риск, что не готов платить), выше — активный.
HIGH_NEED_SCORE_THRESHOLD = 60


def manual_search_links(name: str, city: str | None = None) -> list[str]:
    """Готовые ссылки для РУЧНОЙ проверки компании (никаких запросов с бэкенда).

    Пользователь открывает их в браузере одним кликом. Название/город
    percent-кодируются (quote), поэтому кириллица и пробелы в URL корректны и
    безопасны как значение HTML-атрибута href.
    """
    q_2gis = quote(f"{name} {city}".strip() if city else name, safe="")
    q_ig = quote(name, safe="")
    url_2gis = f"https://{_gis_domain()}/search/{q_2gis}"
    url_ig = f"https://www.instagram.com/explore/search/keyword/?q={q_ig}"
    return [
        f'{E.SEARCH} <a href="{url_2gis}">Найти в 2ГИС</a>',
        f'{E.CAMERA} <a href="{url_ig}">Найти в Instagram</a>',
    ]


def _shorten(text: str, limit: int = 900) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _telegram_profile_link(username: str) -> str:
    clean = username.lstrip("@")
    return f'https://t.me/{quote(clean, safe="")}'


def need_score_line(ai_score: int | None, has_online_booking: bool | None) -> str | None:
    """Текстовая интерпретация под наш оффер (запись через Telegram-бота).

    Не новый вызов LLM и не новое поле в БД — только чтение уже сохранённых
    ai_score и has_online_booking. Пока лид не проанализирован (ai_score is None)
    интерпретировать нечего -> None.
    """
    if ai_score is None:
        return None
    if has_online_booking is True:
        return f"{E.CHECK} Уже есть онлайн-запись — оффер менее актуален"
    if has_online_booking is False:
        if ai_score >= HIGH_NEED_SCORE_THRESHOLD:
            return (
                f"{E.TARGET} Высокий потенциал: активный бизнес без онлайн-записи "
                "— приоритетный лид"
            )
        return (
            f"{E.WARNING} Есть потребность, но бизнес слабый — риск, что не готовы "
            "платить за сервис"
        )
    # has_online_booking is None — сайт есть, но признаков записи не видно (или
    # определить не удалось): нужна ручная проверка.
    return f"{E.EXCLAMATION} Не удалось определить онлайн-запись — требует ручной проверки"


def format_company_card(company: dict, city: str | None = None) -> str:
    """Карточка результата поиска. Только реально пришедшие поля."""
    lines = [f"<b>{escape(company['name'])}</b>"]
    # Тип сущности берётся ТОЛЬКО из OSM-данных (source metadata), не из имени.
    etype = company.get("entity_type")
    if etype:
        lines.append(f"{E.COMPANY} {escape(etype)}")
    if company.get("address"):
        lines.append(f"{E.LOCATION} {escape(company['address'])}")
    if company.get("phone"):
        lines.append(f"{E.PHONE} {escape(company['phone'])}")
    if company.get("website"):
        lines.append(f"{E.LINK} {escape(company['website'])}")
    lines.extend(manual_search_links(company["name"], city))
    return "\n".join(lines)


def format_lead_card(lead: Lead) -> str:
    is_chat_lead = lead.source == "chat_monitor"
    lines = [f"<b>{escape(lead.name)}</b>"]
    if is_chat_lead:
        if lead.niche:
            lines.append(f"{E.PIN} Ниша: {escape(lead.niche)}")
        if lead.source_chat:
            lines.append(f"{E.COMMENT} Чат: {escape(lead.source_chat)}")
        if lead.chat_username:
            lines.append(f'{E.LINK} Автор: <a href="{_telegram_profile_link(lead.chat_username)}">@{escape(lead.chat_username)}</a>')
        elif lead.chat_user_id:
            lines.append(f"{E.IDEA} Telegram user_id: {lead.chat_user_id}")
        if lead.message_date:
            lines.append(f"{E.CALENDAR} Дата сообщения: {lead.message_date:%d.%m.%Y %H:%M}")
        if lead.relevance_score is not None:
            lines.append(f"{E.TARGET} Релевантность: {lead.relevance_score:.2f}")
    if lead.address and not is_chat_lead:
        lines.append(f"{E.LOCATION} {escape(lead.address)}")
    if lead.phone:
        lines.append(f"{E.PHONE} {escape(lead.phone)}")
    if lead.website:
        lines.append(f"{E.LINK} {escape(lead.website)}")
    lines.append(f"{E.PIN} Статус: {STATUS_LABELS.get(lead.status, lead.status)}")
    if lead.ai_score is not None:
        lines.append(f"{E.CHART} AI-оценка: {lead.ai_score}/100")
    if lead.has_online_booking is False:
        lines.append(f"{E.EXCLAMATION} Онлайн-записи нет — можно предложить запись через Telegram-бота")
    elif lead.has_online_booking is True:
        lines.append(f"{E.CHECK} Онлайн-запись есть")
    # Явная строка «насколько нужен мой сервис» (интерпретация ai_score + booking).
    need_line = need_score_line(lead.ai_score, lead.has_online_booking)
    if need_line:
        lines.append(need_line)
    if lead.ai_analysis:
        lines.append(f"\n{escape(lead.ai_analysis)}")
    if is_chat_lead and lead.message_text:
        lines.append(f"\n<b>Сообщение из чата:</b>\n<i>{escape(_shorten(lead.message_text))}</i>")
    if lead.note:
        lines.append(f"\n{E.NOTE} Заметка: {escape(lead.note)}")
    if not is_chat_lead:
        lines.extend([""] + manual_search_links(lead.name))
    return "\n".join(lines)
