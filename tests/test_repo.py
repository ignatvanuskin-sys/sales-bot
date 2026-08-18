"""Тесты CRUD-слоя."""

from datetime import timedelta

import pytest

from db import repo
from db.models import LeadStatus, is_valid_status, utcnow

OWNER = 111


async def test_get_or_create_user_creates_and_is_idempotent(session):
    u1 = await repo.get_or_create_user(session, 42, "alice")
    u2 = await repo.get_or_create_user(session, 42, "alice")
    assert u1.id == u2.id
    assert u1.tg_user_id == 42
    assert u1.username == "alice"


async def test_get_or_create_user_updates_username(session):
    await repo.get_or_create_user(session, 42, "old_name")
    u = await repo.get_or_create_user(session, 42, "new_name")
    assert u.username == "new_name"


async def test_create_and_get_lead(session):
    lead = await repo.create_lead(
        session, OWNER, "Barber Bros", address="ул. Ленина, 1", phone="+7 900 000-00-00"
    )
    assert lead.id is not None
    assert lead.status == LeadStatus.new.value
    assert lead.source == "osm"

    fetched = await repo.get_lead(session, lead.id, OWNER)
    assert fetched is not None
    assert fetched.name == "Barber Bros"


async def test_get_lead_scoped_by_owner(session):
    lead = await repo.create_lead(session, OWNER, "Secret Co")
    other = await repo.get_lead(session, lead.id, OWNER + 1)
    assert other is None


async def test_find_lead_by_name_address_dedupe(session):
    await repo.create_lead(session, OWNER, "Cafe X", address="Main st 5")
    found = await repo.find_lead_by_name_address(session, OWNER, "Cafe X", "Main st 5")
    assert found is not None
    missing = await repo.find_lead_by_name_address(session, OWNER, "Cafe X", "Other st 9")
    assert missing is None


async def test_list_leads_with_status_filter(session):
    a = await repo.create_lead(session, OWNER, "A")
    b = await repo.create_lead(session, OWNER, "B")
    await repo.set_lead_status(session, b.id, OWNER, "written")

    all_leads = await repo.list_leads(session, OWNER)
    assert {lead.name for lead in all_leads} == {"A", "B"}

    new_only = await repo.list_leads(session, OWNER, status="new")
    assert [lead.id for lead in new_only] == [a.id]

    written_only = await repo.list_leads(session, OWNER, status="written")
    assert [lead.id for lead in written_only] == [b.id]


async def test_list_leads_invalid_filter_raises(session):
    with pytest.raises(ValueError):
        await repo.list_leads(session, OWNER, status="hacked")


async def test_set_lead_status_valid_and_invalid(session):
    lead = await repo.create_lead(session, OWNER, "A")
    updated = await repo.set_lead_status(session, lead.id, OWNER, "client")
    assert updated.status == "client"

    with pytest.raises(ValueError):
        await repo.set_lead_status(session, lead.id, OWNER, "not_a_status")


async def test_set_lead_status_missing_lead_returns_none(session):
    result = await repo.set_lead_status(session, 9999, OWNER, "new")
    assert result is None


async def test_set_lead_note(session):
    lead = await repo.create_lead(session, OWNER, "A")
    updated = await repo.set_lead_note(session, lead.id, OWNER, "позвонить в среду")
    assert updated.note == "позвонить в среду"

    missing = await repo.set_lead_note(session, 9999, OWNER, "x")
    assert missing is None


async def test_save_lead_analysis(session):
    lead = await repo.create_lead(session, OWNER, "A")
    updated = await repo.save_lead_analysis(session, lead.id, OWNER, 73, "Слабые места: нет сайта")
    assert updated.ai_score == 73
    assert "нет сайта" in updated.ai_analysis
    # без явного значения -> None (не анализировали online booking)
    assert updated.has_online_booking is None

    missing = await repo.save_lead_analysis(session, 9999, OWNER, 50, "x")
    assert missing is None


async def test_save_lead_analysis_persists_has_online_booking(session):
    lead = await repo.create_lead(session, OWNER, "A")
    updated = await repo.save_lead_analysis(session, lead.id, OWNER, 60, "x", has_online_booking=False)
    assert updated.has_online_booking is False
    # повторный анализ может изменить значение
    again = await repo.save_lead_analysis(session, lead.id, OWNER, 60, "x", has_online_booking=True)
    assert again.has_online_booking is True


async def test_list_leads_only_no_booking_excludes_true_and_null(session):
    no_book = await repo.create_lead(session, OWNER, "NoBooking")
    await repo.save_lead_analysis(session, no_book.id, OWNER, 50, "x", has_online_booking=False)
    has_book = await repo.create_lead(session, OWNER, "HasBooking")
    await repo.save_lead_analysis(session, has_book.id, OWNER, 50, "x", has_online_booking=True)
    unknown = await repo.create_lead(session, OWNER, "Unknown")  # не анализировали -> None
    await repo.save_lead_analysis(session, unknown.id, OWNER, 50, "x")  # has_online_booking=None

    result = await repo.list_leads(session, OWNER, only_no_booking=True)
    names = {lead.name for lead in result}
    assert names == {"NoBooking"}  # ни True, ни None не попадают


async def test_init_db_migrates_missing_has_online_booking_column(tmp_path):
    """Прод-апгрейд: init_db досоздаёт колонку в уже существующей leads-таблице."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from db.base import init_db

    db_file = tmp_path / "old.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    try:
        # Старая схема leads БЕЗ has_online_booking
        async with eng.begin() as c:
            await c.execute(text(
                "CREATE TABLE leads (id INTEGER PRIMARY KEY, owner_tg_id BIGINT, "
                "name VARCHAR, status VARCHAR)"
            ))
        async with eng.begin() as c:
            before = {r[1] for r in (await c.execute(text("PRAGMA table_info(leads)"))).all()}
        assert "has_online_booking" not in before

        await init_db(eng)  # должен добавить колонку
        async with eng.begin() as c:
            after = {r[1] for r in (await c.execute(text("PRAGMA table_info(leads)"))).all()}
        assert "has_online_booking" in after

        await init_db(eng)  # повторный вызов идемпотентен, без падения
    finally:
        await eng.dispose()


async def test_reminders_due_and_mark_sent(session):
    lead = await repo.create_lead(session, OWNER, "A")
    past = await repo.create_reminder(session, lead.id, OWNER, utcnow() - timedelta(minutes=5), "past")
    await repo.create_reminder(session, lead.id, OWNER, utcnow() + timedelta(days=1), "future")

    due = await repo.get_due_reminders(session)
    assert [r.id for r in due] == [past.id]

    await repo.mark_reminder_sent(session, past.id, OWNER)
    due_after = await repo.get_due_reminders(session)
    assert due_after == []


async def test_is_valid_status():
    assert is_valid_status("new")
    assert is_valid_status("rejected")
    assert not is_valid_status("")
    assert not is_valid_status("NEW")
    assert not is_valid_status("random")
