"""Тесты клиента Overpass API. Реальная сеть не используется — всё мокается."""

import asyncio

import aiohttp
import pytest

from services import places
from services.places import (
    MAX_LIMIT,
    Company,
    PlacesError,
    build_query,
    normalize_city,
    parse_elements,
    search_companies,
)


def _element(name=None, **tags):
    t = dict(tags)
    if name:
        t["name"] = name
    return {"type": "node", "id": 1, "tags": t}


# ---------- build_query ----------

def test_build_query_contains_city_and_tags():
    q = build_query("Казань", "shop", "hairdresser")
    assert '"Казань"' in q
    assert 'node["shop"="hairdresser"]' in q
    assert f"out center tags {MAX_LIMIT}" in q


def test_build_query_strips_quotes_injection():
    # Пользователь пытается вырваться из area["name"="..."] и дописать свой QL.
    q = build_query('Kazan"];node["amenity"="bar"];//', "shop", "beauty")
    # Ключевое свойство безопасности: внутри area["name"="..."] не осталось ни
    # одной пользовательской кавычки — строку закрыть раньше времени нельзя,
    # значит инъекция в QL невозможна.
    injected = q.split('area["name"="', 1)[1].split('"]', 1)[0]
    assert '"' not in injected
    # Whitelist-санитайзер (S-1) оставляет только буквы, цифры и безопасные символы.
    # Из 'Kazan"];node["amenity"="bar"];//' остаётся только 'Kazan' (латиница + дефис/точка).
    # Спецсимволы QL (;, [, ], {, }, >, ") полностью удалены.
    assert ';' not in injected
    assert '[' not in injected
    assert ']' not in injected


def test_normalize_city_fixes_case():
    # Регрессия на реальный баг: 'астана' (нижний регистр) не находил area,
    # т.к. OSM хранит 'Астана', а Overpass сравнивает name регистрозависимо.
    assert normalize_city("астана") == "Астана"
    assert normalize_city("АСТАНА") == "Астана"
    assert normalize_city("  астана  ") == "Астана"
    # многословные и через дефис — каждое слово с заглавной
    assert normalize_city("нижний новгород") == "Нижний Новгород"
    assert normalize_city("усть-каменогорск") == "Усть-Каменогорск"
    # уже правильный ввод не ломается
    assert normalize_city("Казань") == "Казань"


def test_build_query_normalizes_astana_lowercase():
    # Именно тот ввод, что дал пользователь: 'астана' + барбершоп (shop=hairdresser).
    q = build_query("астана", "shop", "hairdresser")
    assert 'area["name"="Астана"]' in q       # ушло в OSM-регистре -> area находится
    assert 'area["name"="астана"]' not in q    # исходный нижний регистр не уходит
    assert 'node["shop"="hairdresser"]' in q


async def test_search_astana_barber_sends_normalized_city(monkeypatch):
    """Сквозной путь: search_companies('астана', 'barber') шлёт в Overpass 'Астана'."""
    seen = {}

    async def fake_request(query):
        seen["query"] = query
        return {"elements": [_element("Барбершоп в Астане")]}

    monkeypatch.setattr(places, "_request_overpass", fake_request)
    result = await search_companies("астана", "barber")
    assert 'area["name"="Астана"]' in seen["query"]
    assert [c.name for c in result] == ["Барбершоп в Астане"]


def test_normalize_city_lowercases_connectors():
    # Регрессия на реальный баг живого прогона: 'Ростов-на-Дону' возвращал 0 —
    # str.title() давал 'Ростов-На-Дону', а OSM хранит 'Ростов-на-Дону'
    # (служебное «на» в нижнем регистре) -> area не находится.
    assert normalize_city("Ростов-на-Дону") == "Ростов-на-Дону"
    assert normalize_city("ростов-на-дону") == "Ростов-на-Дону"
    assert normalize_city("РОСТОВ-НА-ДОНУ") == "Ростов-на-Дону"
    assert normalize_city("комсомольск-на-амуре") == "Комсомольск-на-Амуре"
    # транслитерации иностранных названий тоже держат предлог в нижнем регистре
    assert normalize_city("рио-де-жанейро") == "Рио-де-Жанейро"


def test_normalize_city_keeps_non_connector_words_capitalized():
    # Слова, похожие по позиции, но НЕ служебные — остаются с заглавной.
    # (Иначе можно было бы «перелечить» и сломать эти города.)
    assert normalize_city("усть-каменогорск") == "Усть-Каменогорск"
    assert normalize_city("нижний новгород") == "Нижний Новгород"
    assert normalize_city("петропавловск-камчатский") == "Петропавловск-Камчатский"
    # первое слово никогда не опускаем в нижний регистр
    assert normalize_city("надым") == "Надым"


def test_build_query_matches_multilingual_name_keys():
    # Регрессия: 'Усть-Каменогорск' возвращал 0, т.к. основной OSM-тег name у
    # города — казахский ('Өскемен'), а русское имя лежит в name:ru. Запрос
    # должен искать area по name, name:ru и name:en.
    q = build_query("усть-каменогорск", "amenity", "cafe")
    assert 'area["name"="Усть-Каменогорск"]' in q
    assert 'area["name:ru"="Усть-Каменогорск"]' in q
    assert 'area["name:en"="Усть-Каменогорск"]' in q
    # area-выражения объединены в ОДИН набор .a -> тело выполняется по нему один
    # раз, объекты не задваиваются даже при совпадении по нескольким ключам.
    assert ")->.a;" in q
    assert q.count("node[\"amenity\"=\"cafe\"](area.a)") == 1


async def test_search_rostov_sends_lowercase_connector(monkeypatch):
    """Сквозной путь: 'Ростов-на-Дону' уходит в Overpass с «на» в нижнем регистре."""
    seen = {}

    async def fake_request(query):
        seen["query"] = query
        return {"elements": [_element("Кафе на Дону")]}

    monkeypatch.setattr(places, "_request_overpass", fake_request)
    result = await search_companies("ростов-на-дону", "cafe")
    assert 'area["name"="Ростов-на-Дону"]' in seen["query"]
    assert 'area["name"="Ростов-На-Дону"]' not in seen["query"]
    assert [c.name for c in result] == ["Кафе на Дону"]


# ---------- parse_elements ----------

def test_parse_elements_full_fields():
    data = {"elements": [_element(
        "Барбершоп Борода",
        **{"addr:street": "Ленина", "addr:housenumber": "1", "addr:city": "Казань",
           "phone": "+7 900 111-22-33", "website": "https://boroda.ru"},
    )]}
    result = parse_elements(data)
    assert result == [Company(
        name="Барбершоп Борода",
        address="Ленина, 1, Казань",
        phone="+7 900 111-22-33",
        website="https://boroda.ru",
    )]


def test_parse_elements_contact_prefixed_tags():
    data = {"elements": [_element("X", **{"contact:phone": "123", "contact:website": "https://x.ru"})]}
    c = parse_elements(data)[0]
    assert c.phone == "123"
    assert c.website == "https://x.ru"


def test_parse_elements_missing_fields_stay_none():
    data = {"elements": [_element("Only Name")]}
    c = parse_elements(data)[0]
    assert c.address is None
    assert c.phone is None
    assert c.website is None


def test_parse_elements_skips_unnamed():
    data = {"elements": [_element(None, phone="123"), _element("Named")]}
    result = parse_elements(data)
    assert [c.name for c in result] == ["Named"]


def test_parse_elements_respects_max_limit():
    data = {"elements": [_element(f"Company {i}") for i in range(MAX_LIMIT + 50)]}
    result = parse_elements(data)
    assert len(result) == MAX_LIMIT


def test_parse_elements_empty_response():
    assert parse_elements({}) == []
    assert parse_elements({"elements": []}) == []


# ---------- search_companies (с моком HTTP) ----------

async def test_search_companies_happy_path(monkeypatch):
    async def fake_request(query):
        assert "hairdresser" in query
        return {"elements": [_element("Барбер")]}

    monkeypatch.setattr(places, "_request_overpass", fake_request)
    result = await search_companies("Казань", "barber")
    assert [c.name for c in result] == ["Барбер"]


async def test_search_companies_unknown_category():
    with pytest.raises(PlacesError, match="Unknown category"):
        await search_companies("Казань", "spaceships")


async def test_search_companies_propagates_places_error(monkeypatch):
    async def fake_request(query):
        raise PlacesError("Overpass HTTP 504")

    monkeypatch.setattr(places, "_request_overpass", fake_request)
    with pytest.raises(PlacesError, match="504"):
        await search_companies("Казань", "cafe")


# ---------- _request_overpass (с фейковым aiohttp) ----------

class FakeResponse:
    def __init__(self, status=200, json_data=None, json_exc=None, headers=None):
        self.status = status
        self._json_data = json_data
        self._json_exc = json_exc
        self.headers = headers or {}

    async def json(self, content_type=None):
        if self._json_exc:
            raise self._json_exc
        return self._json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, response=None, post_exc=None):
        self._response = response
        self._post_exc = post_exc

    def post(self, url, data=None, headers=None):
        if self._post_exc:
            raise self._post_exc
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _patch_session(monkeypatch, **kwargs):
    monkeypatch.setattr(
        places.aiohttp, "ClientSession", lambda timeout=None: FakeSession(**kwargs)
    )


async def test_request_overpass_ok(monkeypatch):
    _patch_session(monkeypatch, response=FakeResponse(200, {"elements": []}))
    data = await places._request_overpass("query")
    assert data == {"elements": []}


async def test_request_overpass_sends_user_agent(monkeypatch):
    seen = {}

    class HeaderSession(FakeSession):
        def post(self, url, data=None, headers=None):
            seen["headers"] = headers
            return FakeResponse(200, {"elements": []})

    monkeypatch.setattr(places.aiohttp, "ClientSession", lambda timeout=None: HeaderSession())
    assert await places._request_overpass("query") == {"elements": []}
    assert seen["headers"]["User-Agent"].startswith("sales-bot/")
    assert seen["headers"]["Accept"] == "application/json"


async def test_request_overpass_falls_back_after_406(monkeypatch):
    responses = iter([FakeResponse(406), FakeResponse(200, {"elements": []})])
    calls = []

    class SequenceSession(FakeSession):
        def post(self, url, data=None, headers=None):
            calls.append(url)
            return next(responses)

    monkeypatch.setattr(places.aiohttp, "ClientSession", lambda timeout=None: SequenceSession())
    monkeypatch.setattr("config.settings.external_retry_attempts", 0)
    assert await places._request_overpass("query") == {"elements": []}
    assert len(calls) == 2


async def test_request_overpass_http_4xx(monkeypatch):
    _patch_session(monkeypatch, response=FakeResponse(400))
    with pytest.raises(PlacesError, match="HTTP 400"):
        await places._request_overpass("query")


async def test_request_overpass_http_5xx(monkeypatch):
    _patch_session(monkeypatch, response=FakeResponse(504))
    with pytest.raises(PlacesError, match="HTTP 504"):
        await places._request_overpass("query")


async def test_request_overpass_invalid_json(monkeypatch):
    _patch_session(monkeypatch, response=FakeResponse(200, json_exc=ValueError("bad json")))
    with pytest.raises(PlacesError, match="invalid JSON"):
        await places._request_overpass("query")


async def test_request_overpass_timeout(monkeypatch):
    _patch_session(monkeypatch, post_exc=asyncio.TimeoutError())
    with pytest.raises(PlacesError, match="timed out"):
        await places._request_overpass("query")


async def test_request_overpass_network_error(monkeypatch):
    _patch_session(monkeypatch, post_exc=aiohttp.ClientConnectionError("refused"))
    with pytest.raises(PlacesError, match="network error"):
        await places._request_overpass("query")


async def test_request_overpass_retries_transient_5xx(monkeypatch):
    responses = iter([
        FakeResponse(503),
        FakeResponse(200, {"elements": []}),
    ])
    calls = 0

    class SequenceSession(FakeSession):
        def post(self, url, data=None, headers=None):
            nonlocal calls
            calls += 1
            return next(responses)

    monkeypatch.setattr(places.aiohttp, "ClientSession", lambda timeout=None: SequenceSession())
    monkeypatch.setattr("config.settings.external_retry_attempts", 1)
    monkeypatch.setattr("config.settings.external_retry_base_delay_seconds", 0)

    assert await places._request_overpass("query") == {"elements": []}
    assert calls == 2


async def test_request_overpass_does_not_retry_permanent_4xx(monkeypatch):
    calls = 0

    class CountingSession(FakeSession):
        def post(self, url, data=None, headers=None):
            nonlocal calls
            calls += 1
            return FakeResponse(400)

    monkeypatch.setattr(places.aiohttp, "ClientSession", lambda timeout=None: CountingSession())
    monkeypatch.setattr("config.settings.external_retry_attempts", 3)
    with pytest.raises(PlacesError, match="HTTP 400"):
        await places._request_overpass("query")
    assert calls == 1
