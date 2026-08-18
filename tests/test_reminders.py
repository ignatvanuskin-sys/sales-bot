"""Тесты фонового поллера напоминаний: poll_reminders_once и сам reminders_loop."""

import asyncio
from datetime import timedelta

from db import repo
from db.models import utcnow
from services.reminders import poll_reminders_once, reminders_loop

OWNER = 333


class FakeBot:
    def __init__(self, fail=False, fail_times=0):
        self.sent: list[tuple[int, str]] = []
        self.fail = fail
        self.fail_times = fail_times  # падать первые N вызовов, потом работать

    async def send_message(self, chat_id, text):
        if self.fail or self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("telegram is down")
        self.sent.append((chat_id, text))


async def _make_due_reminder(session, text="пора"):
    lead = await repo.create_lead(session, OWNER, "Барбершоп <Точка>")
    return await repo.create_reminder(
        session, lead.id, OWNER, utcnow() - timedelta(minutes=1), text
    )


# ---------- poll_reminders_once ----------

async def test_poll_sends_due_reminders(session_factory):
    async with session_factory() as session:
        await _make_due_reminder(session, "написать снова")

    bot = FakeBot()
    processed = await poll_reminders_once(bot, session_factory)

    assert processed == 1
    assert len(bot.sent) == 1
    chat_id, text = bot.sent[0]
    assert chat_id == OWNER
    assert "написать снова" in text
    # HTML-экранирование названия лида
    assert "&lt;Точка&gt;" in text


async def test_poll_ignores_future_and_sent(session_factory):
    async with session_factory() as session:
        lead = await repo.create_lead(session, OWNER, "Лид")
        await repo.create_reminder(session, lead.id, OWNER, utcnow() + timedelta(days=1), "future")
        sent = await repo.create_reminder(session, lead.id, OWNER, utcnow() - timedelta(days=1), "old")
        await repo.mark_reminder_sent(session, sent.id, OWNER)

    bot = FakeBot()
    processed = await poll_reminders_once(bot, session_factory)
    assert processed == 0
    assert bot.sent == []


async def test_poll_skips_reminder_for_soft_deleted_lead(session_factory):
    """F5: напоминание по soft-deleted лиду НЕ отправляется.

    Пользователь, удаливший лид, ожидает тишины по нему — иначе бот кажется
    игнорирующим удаление и 'пристаёт' вопросами о мёртвых лидах.
    """
    async with session_factory() as session:
        lead = await repo.create_lead(session, OWNER, "Удалённый лид")
        await repo.create_reminder(session, lead.id, OWNER, utcnow() - timedelta(minutes=1), "del")
        await repo.delete_lead(session, lead.id, OWNER)
        # Живое напоминание для контраста
        live = await repo.create_lead(session, OWNER, "Живой лид")
        await repo.create_reminder(session, live.id, OWNER, utcnow() - timedelta(minutes=1), "live")

    bot = FakeBot()
    processed = await poll_reminders_once(bot, session_factory)
    assert processed == 1
    assert len(bot.sent) == 1
    assert "Живой лид" in bot.sent[0][1]


async def test_poll_processes_multiple_due_at_once(session_factory):
    """Несколько просроченных за раз — все обрабатываются, ни одно не пропущено."""
    async with session_factory() as session:
        lead = await repo.create_lead(session, OWNER, "Лид")
        for i in range(5):
            await repo.create_reminder(
                session, lead.id, OWNER, utcnow() - timedelta(minutes=i + 1), f"rem-{i}"
            )

    bot = FakeBot()
    processed = await poll_reminders_once(bot, session_factory)
    assert processed == 5
    assert len(bot.sent) == 5
    sent_texts = "\n".join(t for _, t in bot.sent)
    for i in range(5):
        assert f"rem-{i}" in sent_texts

    # Повторный проход ничего не дублирует
    assert await poll_reminders_once(bot, session_factory) == 0
    assert len(bot.sent) == 5


async def test_poll_failed_send_rolls_back_and_retries(session_factory):
    """Сбой отправки: is_sent откатывается, следующая итерация доставляет напоминание."""
    async with session_factory() as session:
        reminder = await _make_due_reminder(session)
        reminder_id = reminder.id

    bot = FakeBot(fail_times=1)
    processed = await poll_reminders_once(bot, session_factory)  # не должно бросить
    assert processed == 1
    assert bot.sent == []

    # После сбоя напоминание снова в очереди (is_sent откачен)
    async with session_factory() as session:
        due = await repo.get_due_reminders(session)
        assert [r.id for r in due] == [reminder_id]

    # Следующая итерация — доставка успешна и без дублей
    assert await poll_reminders_once(bot, session_factory) == 1
    assert len(bot.sent) == 1
    assert await poll_reminders_once(bot, session_factory) == 0
    assert len(bot.sent) == 1


async def test_poll_second_run_does_not_duplicate(session_factory):
    async with session_factory() as session:
        await _make_due_reminder(session)

    bot = FakeBot()
    await poll_reminders_once(bot, session_factory)
    await poll_reminders_once(bot, session_factory)
    assert len(bot.sent) == 1


# ---------- reminders_loop (тело цикла) ----------

async def test_loop_sends_and_sleeps(session_factory):
    """Одна итерация живого цикла: напоминание отправлено, цикл продолжает работать."""
    async with session_factory() as session:
        await _make_due_reminder(session, "из цикла")

    bot = FakeBot()
    task = asyncio.create_task(reminders_loop(bot, session_factory, interval_seconds=0.01))
    try:
        for _ in range(200):
            if bot.sent:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert len(bot.sent) == 1
    assert "из цикла" in bot.sent[0][1]


async def test_loop_survives_poll_exception(session_factory):
    """Ошибка внутри итерации (упала БД) логируется, цикл не умирает."""
    calls = {"n": 0}

    def broken_then_ok_factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db exploded")
        return session_factory()

    async with session_factory() as session:
        await _make_due_reminder(session, "после сбоя")

    bot = FakeBot()
    task = asyncio.create_task(reminders_loop(bot, broken_then_ok_factory, interval_seconds=0.01))
    try:
        for _ in range(200):
            if bot.sent:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
    # Первая итерация упала, но цикл выжил и доставил напоминание на следующей
    assert calls["n"] >= 2
    assert len(bot.sent) == 1
    assert "после сбоя" in bot.sent[0][1]
