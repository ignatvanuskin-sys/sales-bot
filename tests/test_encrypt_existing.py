"""Тесты scripts.encrypt_existing: dry-run/apply для plaintext message_text."""

from cryptography.fernet import Fernet
from sqlalchemy import text

from db import repo
from db.models import utcnow
from scripts.encrypt_existing import encrypt_plaintext_message_texts
from utils import crypto


async def test_encrypt_existing_dry_run_counts_plaintext(session_factory, session, monkeypatch):
    monkeypatch.setattr(crypto.settings, "pii_encryption_key", "")
    crypto.reset_fernet()
    lead = await repo.create_chat_lead(
        session,
        owner_tg_id=1,
        source_chat="c",
        user_id=1,
        username="u",
        message_text="plaintext",
        message_date=utcnow(),
        relevance_score=0.9,
        llm_reasoning="r",
        message_id=100,
    )
    assert lead.id is not None

    monkeypatch.setattr(crypto.settings, "pii_encryption_key", Fernet.generate_key().decode())
    crypto.reset_fernet()
    counts = await encrypt_plaintext_message_texts(session_factory, dry_run=True)
    assert counts["total_with_text"] == 1
    assert counts["already_encrypted"] == 0
    assert counts["to_encrypt"] == 1
    assert counts["encrypted"] == 0


async def test_encrypt_existing_apply_encrypts_plaintext(session_factory, session, monkeypatch):
    monkeypatch.setattr(crypto.settings, "pii_encryption_key", "")
    crypto.reset_fernet()
    lead = await repo.create_chat_lead(
        session,
        owner_tg_id=1,
        source_chat="c",
        user_id=1,
        username="u",
        message_text="plaintext",
        message_date=utcnow(),
        relevance_score=0.9,
        llm_reasoning="r",
        message_id=101,
    )
    await session.commit()

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto.settings, "pii_encryption_key", key)
    crypto.reset_fernet()
    counts = await encrypt_plaintext_message_texts(session_factory, dry_run=False)
    assert counts["encrypted"] == 1

    raw = (await session.execute(
        text("SELECT message_text FROM leads WHERE id = :id"), {"id": lead.id}
    )).scalar_one()
    assert raw.startswith(crypto.ENC_PREFIX)

    await session.refresh(lead)
    assert lead.message_text == "plaintext"


async def test_encrypt_existing_skips_already_encrypted(session_factory, session, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto.settings, "pii_encryption_key", key)
    crypto.reset_fernet()
    lead = await repo.create_chat_lead(
        session,
        owner_tg_id=1,
        source_chat="c",
        user_id=1,
        username="u",
        message_text="encrypted now",
        message_date=utcnow(),
        relevance_score=0.9,
        llm_reasoning="r",
        message_id=102,
    )
    await session.commit()
    assert lead.id is not None

    counts = await encrypt_plaintext_message_texts(session_factory, dry_run=True)
    assert counts["already_encrypted"] == 1
    assert counts["to_encrypt"] == 0
