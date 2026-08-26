"""Тесты хендлеров прямым вызовом функций (без живого Telegram).

session_factory в модулях хендлеров подменяется на тестовую (in-memory SQLite),
внешние сервисы (Overpass, Claude) — на моки.
"""

import pytest

import handlers.analysis as h_analysis
import handlers.common as h_common
import handlers.crm as h_crm
import handlers.menu as h_menu
import handlers.messages as h_messages
import handlers.search as h_search
import handlers.start as h_start
from db import repo
from services.ai import AIError, AIRateLimitError
from services.places import Company, PlacesError
from states.fsm import ReminderFSM, SearchFSM
from tests.fakes import FakeCallback, FakeMessage, FakeState

USER = 100


@pytest.fixture(autouse=True)
def _patch_session_factory(monkeypatch, session_factory):
    for mod in (h_start, h_search, h_analysis, h_crm, h_messages):
        monkeypatch.setattr(mod, "session_factory", session_factory)


def _companies(n=2):
    return [Company(name=f"Компания {i}", address=f"Улица {i}", phone="+7900", website=None) for i in range(n)]


# ---------- start / menu ----------

async def test_cmd_start_registers_user_and_shows_menu(session_factory):
    msg = FakeMessage(text="/start", user_id=USER)
    await h_start.cmd_start(msg)
    assert "AI Sales Agent" in msg.last_text()
    async with session_factory() as session:
        user = await repo.get_or_create_user(session, USER)
        assert user.tg_user_id == USER


async def test_menu_settings_and_noop():
    cb = FakeCallback("menu:settings", USER)
    await h_menu.show_settings(cb)
    assert "Настройки" in cb.message.last_text()
    cb2 = FakeCallback("noop", USER)
    await h_menu.noop(cb2)
    assert cb2.answers == [(None, False)]


async def test_menu_main_clears_state():
    state = FakeState(data={"junk": 1}, state="something")
    cb = FakeCallback("menu:main", USER)
    await h_menu.show_main_menu(cb, state)
    assert state.data == {} and state.state is None
    assert "Главное меню" in cb.message.texts("edit_text")[-1]


async def test_cmd_menu_returns_to_main():
    from states.fsm import SearchFSM
    state = FakeState(data={"results": [1]}, state=SearchFSM.browsing)
    msg = FakeMessage(text="/menu", user_id=USER)
    await h_common.cmd_menu(msg, state)
    assert state.data == {} and state.state is None
    assert "Главное меню" in msg.last_text()


async def test_cmd_search_sets_waiting_city():
    from states.fsm import SearchFSM
    state = FakeState()
    msg = FakeMessage(text="/search", user_id=USER)
    await h_common.cmd_search(msg, state)
    assert state.state == SearchFSM.waiting_city
    assert "городе" in msg.last_text()


async def test_cmd_leads_shows_filters():
    state = FakeState()
    msg = FakeMessage(text="/leads", user_id=USER)
    await h_common.cmd_leads(msg, state)
    assert state.state is None
    assert "Мои лиды" in msg.last_text()


# ---------- search ----------

async def test_start_search_asks_city():
    state = FakeState()
    cb = FakeCallback("menu:search", USER)
    await h_search.start_search(cb, state)
    assert state.state == SearchFSM.waiting_city
    assert "городе" in cb.message.last_text()


async def test_city_received_empty_reasks():
    state = FakeState()
    msg = FakeMessage(text="   ", user_id=USER)
    await h_search.city_received(msg, state)
    assert "Не понял город" in msg.last_text()
    assert "city" not in state.data


async def test_city_received_valid_moves_to_category():
    state = FakeState()
    msg = FakeMessage(text="Казань", user_id=USER)
    await h_search.city_received(msg, state)
    assert state.data["city"] == "Казань"
    assert state.state == SearchFSM.waiting_category
    assert "категорию" in msg.last_text()


async def test_category_unknown_slug_alert():
    state = FakeState(data={"city": "Казань"})
    cb = FakeCallback("cat:hacked", USER)
    await h_search.category_chosen(cb, state)
    assert any("Неизвестная категория" in t for t in cb.alert_texts())


async def test_category_overpass_down_shows_friendly_error(monkeypatch):
    async def boom(city, slug):
        raise PlacesError("HTTP 504")
    monkeypatch.setattr(h_search, "search_companies", boom)

    state = FakeState(data={"city": "Казань"})
    cb = FakeCallback("cat:barber", USER)
    await h_search.category_chosen(cb, state)
    assert "Сервис поиска сейчас недоступен" in cb.message.last_text()
    assert state.state is None  # state очищен, бот жив


async def test_category_empty_city_results(monkeypatch):
    async def empty(city, slug):
        return []
    monkeypatch.setattr(h_search, "search_companies", empty)

    state = FakeState(data={"city": "ыыыы"})
    cb = FakeCallback("cat:barber", USER)
    await h_search.category_chosen(cb, state)
    assert "ничего не нашлось" in cb.message.last_text()


async def test_category_success_shows_first_card(monkeypatch):
    async def found(city, slug):
        return _companies(3)
    monkeypatch.setattr(h_search, "search_companies", found)

    state = FakeState(data={"city": "Казань"})
    cb = FakeCallback("cat:barber", USER)
    await h_search.category_chosen(cb, state)
    assert state.state == SearchFSM.browsing
    assert len(state.data["results"]) == 3
    assert "Компания 0" in cb.message.last_text()


async def test_paginate_valid_and_invalid():
    state = FakeState(data={"results": [c.to_dict() for c in _companies(2)], "saved_leads": {}})
    cb = FakeCallback("spg:1", USER)
    await h_search.paginate(cb, state)
    assert "Компания 1" in cb.message.texts("edit_text")[-1]

    cb_bad = FakeCallback("spg:abc", USER)
    await h_search.paginate(cb_bad, state)
    assert any("Некорректные данные" in t for t in cb_bad.alert_texts())


async def test_paginate_stale_results():
    state = FakeState(data={"results": [], "saved_leads": {}})
    cb = FakeCallback("spg:0", USER)
    await h_search.paginate(cb, state)
    assert "устарели" in cb.message.last_text()


async def test_search_list_open_shows_card_and_back_returns(monkeypatch):
    """Компактный список: открыть результат -> карточка; slbk -> назад в список."""
    companies = []
    for i in range(7):
        d = {"name": f"Компания {i}", "address": None, "phone": None,
             "website": None, "entity_type": "Барбершоп/парикмахерская"}
        companies.append(d)

    state = FakeState(data={"results": companies, "saved_leads": {}, "city": "Астана"})
    cb = FakeCallback("slo:0", USER)
    await h_search.search_list_open(cb, state)
    # открылась карточка с типом
    assert "Компания 0" in cb.message.texts("edit_text")[-1]
    assert "Барбершоп/парикмахерская" in cb.message.texts("edit_text")[-1]

    # назад в список
    cb_back = FakeCallback("slbk", USER, message=cb.message)
    await h_search.search_list_back_to_list(cb_back, state)
    out = cb_back.message.texts("edit_text")[-1]
    assert "Результаты поиска" in out
    assert "7" in out  # общее количество
    assert "Компания 0" in out


async def test_search_list_paginate_invalid():
    state = FakeState(data={"results": [{"name": "X", "address": None, "phone": None,
                                          "website": None}], "saved_leads": {}, "city": "Астана"})
    cb = FakeCallback("slp:abc", USER)
    await h_search.search_list_paginate(cb, state)
    assert any("Некорректные данные" in t for t in cb.alert_texts())


async def test_save_from_search_dedup(session_factory):
    state = FakeState(data={"results": [c.to_dict() for c in _companies(1)], "saved_leads": {}})
    cb = FakeCallback("ssv:0", USER)
    await h_search.save_from_search(cb, state)
    assert "Сохранено в лиды" in cb.answers[-1][0]

    # Повторное сохранение того же результата — дубль не создаётся
    cb2 = FakeCallback("ssv:0", USER, message=cb.message)
    await h_search.save_from_search(cb2, state)
    async with session_factory() as session:
        leads = await repo.list_leads(session, USER)
    assert len(leads) == 1


async def test_save_from_search_out_of_range():
    state = FakeState(data={"results": [c.to_dict() for c in _companies(1)], "saved_leads": {}})
    cb = FakeCallback("ssv:99", USER)
    await h_search.save_from_search(cb, state)
    assert any("устарели" in t for t in cb.alert_texts())


# ---------- analysis ----------

async def test_analyze_from_lead_success(monkeypatch, session_factory):
    async def fake_analyze(name, address=None, phone=None, website=None):
        return 85, "Слабые места:\n• нет сайта", False
    monkeypatch.setattr(h_analysis, "analyze_company", fake_analyze)

    async with session_factory() as session:
        lead = await repo.create_lead(session, USER, "Барбершоп")
    cb = FakeCallback(f"anl:{lead.id}", USER)
    await h_analysis.analyze_from_lead(cb)
    card = cb.message.last_text()
    assert "85/100" in card and "нет сайта" in card

    async with session_factory() as session:
        saved = await repo.get_lead(session, lead.id, USER)
        assert saved.ai_score == 85


async def test_analyze_invalid_api_key_friendly_error(monkeypatch, session_factory):
    async def fail(name, address=None, phone=None, website=None):
        raise AIError("invalid x-api-key")
    monkeypatch.setattr(h_analysis, "analyze_company", fail)

    async with session_factory() as session:
        lead = await repo.create_lead(session, USER, "Барбершоп")
    cb = FakeCallback(f"anl:{lead.id}", USER)
    await h_analysis.analyze_from_lead(cb)
    assert h_analysis.MSG_AI_FAILED in cb.message.texts("edit_text")[-1]


async def test_analyze_rate_limit_separate_message(monkeypatch, session_factory):
    async def limited(name, address=None, phone=None, website=None):
        raise AIRateLimitError("429")
    monkeypatch.setattr(h_analysis, "analyze_company", limited)

    async with session_factory() as session:
        lead = await repo.create_lead(session, USER, "Барбершоп")
    cb = FakeCallback(f"anl:{lead.id}", USER)
    await h_analysis.analyze_from_lead(cb)
    assert h_analysis.MSG_RATE_LIMIT in cb.message.texts("edit_text")[-1]


async def test_analyze_lead_not_found():
    cb = FakeCallback("anl:99999", USER)
    await h_analysis.analyze_from_lead(cb)
    assert "Лид не найден" in cb.message.texts("edit_text")[-1]


async def test_analyze_invalid_callback_data():
    cb = FakeCallback("anl:abc", USER)
    await h_analysis.analyze_from_lead(cb)
    assert any("Некорректные данные" in t for t in cb.alert_texts())


async def test_analyze_from_search_autosaves(monkeypatch, session_factory):
    async def fake_analyze(name, address=None, phone=None, website=None):
        return 40, "анализ", None
    monkeypatch.setattr(h_analysis, "analyze_company", fake_analyze)

    state = FakeState(data={"results": [c.to_dict() for c in _companies(1)], "saved_leads": {}})
    cb = FakeCallback("san:0", USER)
    await h_analysis.analyze_from_search(cb, state)
    async with session_factory() as session:
        leads = await repo.list_leads(session, USER)
    assert len(leads) == 1 and leads[0].ai_score == 40


# ---------- crm ----------

async def _lead(session_factory, **kwargs):
    async with session_factory() as session:
        return await repo.create_lead(session, USER, "Барбершоп", **kwargs)


async def test_leads_empty_filter_message():
    cb = FakeCallback("leads:client", USER)
    await h_crm.list_leads_filtered(cb)
    assert "лидов пока нет" in cb.message.last_text()


async def test_leads_invalid_filter_alert():
    cb = FakeCallback("leads:hacked", USER)
    await h_crm.list_leads_filtered(cb)
    assert h_crm.MSG_INVALID_STATUS in cb.alert_texts()


async def test_leads_list_and_open_card(session_factory):
    lead = await _lead(session_factory)
    cb = FakeCallback("leads:all", USER)
    await h_crm.list_leads_filtered(cb)
    assert "Лиды (Все): 1" in cb.message.last_text()

    cb2 = FakeCallback(f"lead:{lead.id}", USER)
    await h_crm.show_lead_card(cb2, FakeState())
    assert "Барбершоп" in cb2.message.last_text()


async def test_leads_filter_no_booking_shows_only_false(session_factory):
    async with session_factory() as session:
        a = await repo.create_lead(session, USER, "БезЗаписи")
        await repo.save_lead_analysis(session, a.id, USER, 50, "x", has_online_booking=False)
        b = await repo.create_lead(session, USER, "СЗаписью")
        await repo.save_lead_analysis(session, b.id, USER, 50, "x", has_online_booking=True)
        await repo.create_lead(session, USER, "НеАнализировали")  # has_online_booking=None

    cb = FakeCallback("leads:no_booking", USER)
    await h_crm.list_leads_filtered(cb)
    assert "Без онлайн-записи" in cb.message.last_text()

    markup = cb.message.log[-1][2]
    btn_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("БезЗаписи" in t for t in btn_texts)
    assert not any("СЗаписью" in t for t in btn_texts)       # есть запись -> исключён
    assert not any("НеАнализировали" in t for t in btn_texts)  # None -> исключён


async def test_change_status_valid(session_factory):
    lead = await _lead(session_factory)
    cb = FakeCallback(f"sts:{lead.id}:written", USER)
    await h_crm.change_status(cb)
    assert any("Написали" in (t or "") for t, _ in cb.answers)
    async with session_factory() as session:
        updated = await repo.get_lead(session, lead.id, USER)
        assert updated.status == "written"


async def test_change_status_invalid_callback(session_factory):
    """Модифицированный callback sts:<id>:hacked — валидация, не Exception."""
    lead = await _lead(session_factory)
    cb = FakeCallback(f"sts:{lead.id}:hacked", USER)
    await h_crm.change_status(cb)
    assert h_crm.MSG_INVALID_STATUS in cb.alert_texts()
    async with session_factory() as session:
        unchanged = await repo.get_lead(session, lead.id, USER)
        assert unchanged.status == "new"


async def test_change_status_foreign_lead_hidden(session_factory):
    """Чужой лид (другой owner) недоступен."""
    async with session_factory() as session:
        foreign = await repo.create_lead(session, 999, "Чужой")
    cb = FakeCallback(f"sts:{foreign.id}:written", USER)
    await h_crm.change_status(cb)
    assert any("не найден" in t for t in cb.alert_texts())


async def test_note_flow(session_factory):
    lead = await _lead(session_factory)
    state = FakeState()
    cb = FakeCallback(f"note:{lead.id}", USER)
    await h_crm.note_start(cb, state)
    assert state.data["note_lead_id"] == lead.id

    # пустая заметка
    empty = FakeMessage(text="  ", user_id=USER)
    await h_crm.note_received(empty, state)
    assert "Пустая заметка" in empty.last_text()

    # валидная заметка
    msg = FakeMessage(text="перезвонить в пятницу", user_id=USER)
    await h_crm.note_received(msg, state)
    assert "перезвонить в пятницу" in msg.last_text()


async def test_reminder_days_valid_and_invalid(session_factory):
    lead = await _lead(session_factory)
    cb = FakeCallback(f"remd:{lead.id}:3", USER)
    await h_crm.reminder_days(cb)
    assert any("Напомню через 3" in (t or "") for t, _ in cb.answers)

    cb_bad = FakeCallback(f"remd:{lead.id}:99", USER)
    await h_crm.reminder_days(cb_bad)
    assert any("Некорректный интервал" in t for t in cb_bad.alert_texts())


async def test_reminder_custom_date_validation(session_factory):
    lead = await _lead(session_factory)
    state = FakeState(data={"reminder_lead_id": lead.id})

    bad = FakeMessage(text="завтра", user_id=USER)
    await h_crm.reminder_custom_received(bad, state)
    assert "Не понял дату" in bad.last_text()

    past = FakeMessage(text="01.01.2020 10:00", user_id=USER)
    await h_crm.reminder_custom_received(past, state)
    assert "уже в прошлом" in past.last_text()

    ok = FakeMessage(text="25.12.2099 15:30", user_id=USER)
    await h_crm.reminder_custom_received(ok, state)
    assert "Напомню 25.12.2099 15:30" in ok.last_text()


async def test_reminder_menu_opens(session_factory):
    lead = await _lead(session_factory)
    cb = FakeCallback(f"rem:{lead.id}", USER)
    await h_crm.reminder_menu(cb)
    assert "Когда напомнить" in cb.message.last_text()


# ---------- crm: непокрытые ветки (меню лидов, статусы, заметки, напоминания) ----------

async def test_show_leads_menu_clears_state_and_shows_filters():
    state = FakeState(data={"junk": 1}, state="something")
    cb = FakeCallback("menu:leads", USER)
    await h_crm.show_leads_menu(cb, state)
    assert state.data == {} and state.state is None
    assert "Мои лиды" in cb.message.last_text()
    # отрисована клавиатура фильтров (leads_filter_kb)
    assert cb.message.log[-1][2] is not None


async def test_show_lead_card_invalid_id_alert():
    cb = FakeCallback("lead:abc", USER)
    await h_crm.show_lead_card(cb, FakeState())
    assert h_crm.MSG_BAD_DATA in cb.alert_texts()


async def test_show_lead_card_not_found_alert():
    cb = FakeCallback("lead:99999", USER)
    await h_crm.show_lead_card(cb, FakeState())
    assert h_crm.MSG_LEAD_NOT_FOUND in cb.alert_texts()


async def test_change_status_malformed_too_few_parts(session_factory):
    lead = await _lead(session_factory)
    cb = FakeCallback(f"sts:{lead.id}", USER)  # нет третьей части (статуса)
    await h_crm.change_status(cb)
    assert h_crm.MSG_BAD_DATA in cb.alert_texts()


async def test_change_status_non_int_lead_id():
    cb = FakeCallback("sts:abc:written", USER)
    await h_crm.change_status(cb)
    assert h_crm.MSG_BAD_DATA in cb.alert_texts()


async def test_status_menu_opens_keyboard(session_factory):
    lead = await _lead(session_factory)
    cb = FakeCallback(f"st:{lead.id}", USER)
    await h_crm.status_menu(cb)
    # клавиатура статусов отрисована через edit_reply_markup
    assert any(kind == "edit_markup" for kind, _, _ in cb.message.log)


async def test_status_menu_invalid_id_alert():
    cb = FakeCallback("st:abc", USER)
    await h_crm.status_menu(cb)
    assert h_crm.MSG_BAD_DATA in cb.alert_texts()


async def test_note_start_invalid_id_alert():
    cb = FakeCallback("note:abc", USER)
    await h_crm.note_start(cb, FakeState())
    assert h_crm.MSG_BAD_DATA in cb.alert_texts()


async def test_note_start_not_found_alert():
    cb = FakeCallback("note:99999", USER)
    await h_crm.note_start(cb, FakeState())
    assert h_crm.MSG_LEAD_NOT_FOUND in cb.alert_texts()


async def test_note_received_lead_deleted_returns_not_found():
    # note_lead_id указывает на несуществующий лид -> set_lead_note вернёт None
    state = FakeState(data={"note_lead_id": 99999})
    msg = FakeMessage(text="заметка", user_id=USER)
    await h_crm.note_received(msg, state)
    assert "Лид не найден" in msg.last_text()


async def test_reminder_days_malformed_and_non_int(session_factory):
    lead = await _lead(session_factory)
    cb = FakeCallback(f"remd:{lead.id}", USER)  # нет числа дней
    await h_crm.reminder_days(cb)
    assert h_crm.MSG_BAD_DATA in cb.alert_texts()

    cb2 = FakeCallback("remd:abc:xyz", USER)  # нечисловые части
    await h_crm.reminder_days(cb2)
    assert h_crm.MSG_BAD_DATA in cb2.alert_texts()


async def test_reminder_days_lead_not_found_alert():
    cb = FakeCallback("remd:99999:3", USER)
    await h_crm.reminder_days(cb)
    assert h_crm.MSG_LEAD_NOT_FOUND in cb.alert_texts()


async def test_reminder_custom_start_sets_state(session_factory):
    lead = await _lead(session_factory)
    state = FakeState()
    cb = FakeCallback(f"remc:{lead.id}", USER)
    await h_crm.reminder_custom_start(cb, state)
    assert state.state == ReminderFSM.waiting_date
    assert state.data["reminder_lead_id"] == lead.id
    assert "ДД.ММ.ГГГГ" in cb.message.last_text()


async def test_reminder_custom_start_invalid_id_alert():
    cb = FakeCallback("remc:abc", USER)
    await h_crm.reminder_custom_start(cb, FakeState())
    assert h_crm.MSG_BAD_DATA in cb.alert_texts()


async def test_reminder_custom_received_lead_not_found_clears_state():
    state = FakeState(data={"reminder_lead_id": 99999}, state=ReminderFSM.waiting_date)
    msg = FakeMessage(text="25.12.2099 15:30", user_id=USER)
    await h_crm.reminder_custom_received(msg, state)
    assert "Лид не найден" in msg.last_text()
    assert state.state is None  # state очищен


async def test_reminder_menu_invalid_id_alert():
    cb = FakeCallback("rem:abc", USER)
    await h_crm.reminder_menu(cb)
    assert h_crm.MSG_BAD_DATA in cb.alert_texts()


# ---------- messages ----------

async def test_generate_requires_analysis(session_factory):
    lead = await _lead(session_factory)  # без ai_analysis
    cb = FakeCallback(f"gen:{lead.id}", USER)
    await h_messages.generate_for_lead(cb, FakeState())
    assert any("Сначала сделай AI-анализ" in t for t in cb.alert_texts())


async def test_generate_success_escapes_html(monkeypatch, session_factory):
    async def fake_gen(name, analysis):
        return "Короткий <текст>", "Длинный & текст"
    monkeypatch.setattr(h_messages, "generate_messages", fake_gen)

    async with session_factory() as session:
        lead = await repo.create_lead(session, USER, "Салон <A&B>")
        await repo.save_lead_analysis(session, lead.id, USER, 50, "анализ")

    state = FakeState()
    cb = FakeCallback(f"gen:{lead.id}", USER)
    await h_messages.generate_for_lead(cb, state)
    out = cb.message.texts("edit_text")[-1]
    assert "&lt;текст&gt;" in out and "&amp;" in out and "<текст>" not in out
    assert state.data["gen_short"] == "Короткий <текст>"


async def test_generate_rate_limit(monkeypatch, session_factory):
    async def limited(name, analysis):
        raise AIRateLimitError("429")
    monkeypatch.setattr(h_messages, "generate_messages", limited)

    async with session_factory() as session:
        lead = await repo.create_lead(session, USER, "Салон")
        await repo.save_lead_analysis(session, lead.id, USER, 50, "анализ")

    cb = FakeCallback(f"gen:{lead.id}", USER)
    await h_messages.generate_for_lead(cb, FakeState())
    assert h_messages.MSG_RATE_LIMIT in cb.message.texts("edit_text")[-1]


async def test_edit_message_flow(monkeypatch, session_factory):
    state = FakeState(data={"gen_short": "s", "gen_long": "l", "gen_lead_id": 1, "gen_lead_name": "X"})
    cb = FakeCallback("edm:1:short", USER)
    await h_messages.edit_message_start(cb, state)
    assert state.data["edit_which"] == "short"

    # пустой текст отклоняется
    empty = FakeMessage(text="", user_id=USER)
    await h_messages.edit_message_received(empty, state)
    assert "Пустой текст не подойдёт" in empty.last_text()

    # валидный текст заменяет короткий вариант
    msg = FakeMessage(text="новый короткий", user_id=USER)
    await h_messages.edit_message_received(msg, state)
    assert state.data["gen_short"] == "новый короткий"
    assert state.data["gen_long"] == "l"


async def test_edit_message_stale_context():
    state = FakeState(data={"gen_lead_id": 42})
    cb = FakeCallback("edm:1:short", USER)  # другой lead_id
    await h_messages.edit_message_start(cb, state)
    assert any("сгенерируй сообщения заново" in t for t in cb.alert_texts())
