"""Тесты CRM-логики: статусы, фильтры, заметки, парсинг даты, карточки."""

from datetime import datetime

import pytest

from db import repo
from db.models import STATUS_LABELS, VALID_STATUSES, LeadStatus
from handlers.company import (
    HIGH_NEED_SCORE_THRESHOLD,
    format_company_card,
    format_lead_card,
    manual_search_links,
    need_score_line,
)
from handlers.crm import parse_custom_date

OWNER = 222


def test_all_statuses_have_labels():
    assert set(STATUS_LABELS) == VALID_STATUSES
    assert VALID_STATUSES == {"new", "written", "replied", "client", "rejected"}


async def test_full_status_lifecycle(session):
    lead = await repo.create_lead(session, OWNER, "Лид")
    for status in ("written", "replied", "client"):
        lead = await repo.set_lead_status(session, lead.id, OWNER, status)
        assert lead.status == status


async def test_status_change_scoped_by_owner(session):
    lead = await repo.create_lead(session, OWNER, "Лид")
    result = await repo.set_lead_status(session, lead.id, OWNER + 1, "client")
    assert result is None
    same = await repo.get_lead(session, lead.id, OWNER)
    assert same.status == LeadStatus.new.value


# ---------- parse_custom_date ----------

def test_parse_custom_date_full():
    assert parse_custom_date("25.12.2026 15:30") == datetime(2026, 12, 25, 15, 30)


def test_parse_custom_date_date_only_defaults_10am():
    assert parse_custom_date("25.12.2026") == datetime(2026, 12, 25, 10, 0)


@pytest.mark.parametrize("raw", ["", "завтра", "2026-12-25", "32.13.2026", "25.12.26", "25/12/2026"])
def test_parse_custom_date_invalid(raw):
    assert parse_custom_date(raw) is None


def test_parse_custom_date_strips_whitespace():
    assert parse_custom_date("  25.12.2026 15:30  ") is not None


# ---------- карточки ----------

def test_format_company_card_hides_empty_fields():
    card = format_company_card({"name": "Кафе", "address": None, "phone": None, "website": None})
    assert "Кафе" in card
    assert "None" not in card
    assert "📞" not in card
    assert "🌐" not in card


def test_format_company_card_shows_present_fields():
    card = format_company_card(
        {"name": "Кафе", "address": "Ленина 1", "phone": "+7 900", "website": "https://x.ru"}
    )
    assert "Ленина 1" in card
    assert "+7 900" in card
    assert "https://x.ru" in card


def test_format_company_card_escapes_html():
    card = format_company_card({"name": "Bar <b>&</b>", "address": None, "phone": None, "website": None})
    assert "&lt;b&gt;" in card
    assert "&amp;" in card


async def test_format_lead_card_with_analysis_and_note(session):
    lead = await repo.create_lead(session, OWNER, "Лид & Ко")
    lead = await repo.save_lead_analysis(session, lead.id, OWNER, 64, "Слабые места: <нет сайта>")
    lead = await repo.set_lead_note(session, lead.id, OWNER, "перезвонить")
    card = format_lead_card(lead)
    assert "64/100" in card
    assert "&amp; Ко" in card
    assert "&lt;нет сайта&gt;" in card
    assert "перезвонить" in card
    assert STATUS_LABELS["new"] in card


async def test_format_lead_card_without_analysis(session):
    lead = await repo.create_lead(session, OWNER, "Лид")
    card = format_lead_card(lead)
    assert "AI-оценка" not in card
    assert "None" not in card


# ---------- ссылки для ручной проверки (2ГИС / Instagram) ----------

def test_manual_search_links_encode_name_and_city():
    links = manual_search_links("Кафе Бар", "Астана")
    joined = "\n".join(links)
    # 2ГИС: название + город, пробелы -> %20, кириллица percent-кодирована
    assert "https://2gis.kz/search/" in joined
    assert "%D0%9A%D0%B0%D1%84%D0%B5%20%D0%91%D0%B0%D1%80%20%D0%90%D1%81%D1%82%D0%B0%D0%BD%D0%B0" in joined
    # Instagram: поиск по названию
    assert "https://www.instagram.com/explore/search/keyword/?q=" in joined
    # никаких сырых пробелов/кавычек в href (безопасно как HTML-атрибут)
    assert 'href="' in joined and " " not in joined.split('href="')[1].split('"')[0]


def test_manual_search_links_without_city_uses_name_only():
    links = manual_search_links("Барбершоп")
    joined = "\n".join(links)
    assert "https://2gis.kz/search/%D0%91" in joined  # только название
    assert "None" not in joined


def test_format_company_card_includes_manual_links():
    card = format_company_card(
        {"name": "Кафе", "address": None, "phone": None, "website": None}, city="Алматы"
    )
    assert "2gis.kz/search/" in card
    assert "instagram.com/explore/search/keyword/?q=" in card
    assert "None" not in card


def test_entity_type_from_tags_not_name():
    """Тип показывается ТОЛЬКО из OSM-данных, а не выводится из имени."""
    from services.places import _entity_type

    # 'Саша' — реальный shop=hairdresser барбершоп в OSM: тип=барбершоп.
    assert _entity_type({"name": "Саша", "shop": "hairdresser"}) == "Барбершоп/парикмахерская"
    assert _entity_type({"name": "Gentleman", "shop": "hairdresser"}) == "Барбершоп/парикмахерская"
    # Другие категории из наших тагов.
    assert _entity_type({"name": "Кафе", "amenity": "cafe"}) == "Кафе"
    assert _entity_type({"name": "Фитнес", "leisure": "fitness_centre"}) == "Фитнес-клуб"
    # Неизвестный тег — возвращаем сам тег; отсутствие тега — None (прячем).
    assert _entity_type({"name": "X", "shop": "unknown_tag"}) == "Unknown tag"
    assert _entity_type({"name": "Саша"}) is None


def test_company_card_shows_entity_type():
    card = format_company_card(
        {"name": "Саша", "entity_type": "Барбершоп/парикмахерская",
         "address": None, "phone": None, "website": None}
    )
    assert "Барбершоп/парикмахерская" in card
    # Не пишем «Человек» и не выводим 'None'.
    assert "Человек" not in card
    assert "None" not in card


def test_company_card_hides_unknown_type():
    card = format_company_card(
        {"name": "Саша", "address": None, "phone": None, "website": None}
    )
    assert "Барбершоп" not in card
    assert "None" not in card


# ---------- строка «насколько нужен мой сервис» ----------

def test_need_score_line_not_analyzed_is_none():
    assert need_score_line(None, None) is None
    assert need_score_line(None, False) is None


def test_need_score_line_has_booking_offer_less_relevant():
    line = need_score_line(90, True)
    assert line is not None and "менее актуален" in line


def test_need_score_line_no_booking_high_score_priority():
    line = need_score_line(HIGH_NEED_SCORE_THRESHOLD, False)
    assert "приоритетный лид" in line


def test_need_score_line_no_booking_low_score_weak_business():
    line = need_score_line(HIGH_NEED_SCORE_THRESHOLD - 1, False)
    assert "бизнес слабый" in line


def test_need_score_line_unknown_booking_manual_check():
    line = need_score_line(70, None)
    assert "Не удалось определить" in line


async def test_format_lead_card_shows_priority_line(session):
    # Проанализированный активный бизнес без онлайн-записи -> приоритетный лид.
    lead = await repo.create_lead(session, OWNER, "Активное Кафе")
    lead = await repo.save_lead_analysis(session, lead.id, OWNER, 80, "анализ", has_online_booking=False)
    card = format_lead_card(lead)
    assert "приоритетный лид" in card
    assert "2gis.kz/search/" in card  # ссылки для ручной проверки тоже в карточке
