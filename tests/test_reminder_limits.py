"""Anti-DoS лимит активных напоминаний на пользователя."""

from datetime import timedelta

from db import repo
from db.models import utcnow
from handlers import crm as h_crm
from tests.fakes import FakeCallback, FakeMessage, FakeState


OWNER = 777_123


async def test_count_active_reminders_ignores_sent(session):
    lead = await repo.create_lead(session, OWNER, "Limit Co")
    r1 = await repo.create_reminder(session, lead.id, OWNER, utcnow() + timedelta(days=1), "active")
    r2 = await repo.create_reminder(session, lead.id, OWNER, utcnow() + timedelta(days=1), "sent")
    await repo.mark_reminder_sent(session, r2.id, OWNER)

    assert await repo.count_active_reminders(session, OWNER) == 1
    assert r1.id is not None


async def test_reminder_days_blocks_when_user_limit_reached(session_factory, monkeypatch):
    monkeypatch.setattr(h_crm, "session_factory", session_factory)
    monkeypatch.setattr(h_crm.settings, "max_active_reminders_per_user", 1)
    async with session_factory() as session:
        lead = await repo.create_lead(session, OWNER, "Limit Co")
        await repo.create_reminder(session, lead.id, OWNER, utcnow() + timedelta(days=1), "existing")

    cb = FakeCallback(f"remd:{lead.id}:1", OWNER)
    await h_crm.reminder_days(cb)
    assert any("Слишком много активных напоминаний" in t for t in cb.alert_texts())


async def test_reminder_custom_blocks_when_user_limit_reached(session_factory, monkeypatch):
    monkeypatch.setattr(h_crm, "session_factory", session_factory)
    monkeypatch.setattr(h_crm.settings, "max_active_reminders_per_user", 1)
    async with session_factory() as session:
        lead = await repo.create_lead(session, OWNER, "Limit Co")
        await repo.create_reminder(session, lead.id, OWNER, utcnow() + timedelta(days=1), "existing")

    state = FakeState(data={"reminder_lead_id": lead.id})
    msg = FakeMessage("25.12.2026 15:30", user_id=OWNER)
    await h_crm.reminder_custom_received(msg, state)
    assert "Слишком много активных напоминаний" in msg.last_text()
    assert state.state is None
