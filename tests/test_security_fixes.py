"""Тесты security-фиксов аудита (SEC-FIX 1-6 + CSV escaping)."""

from datetime import timedelta

from sqlalchemy import select, text

from db import repo
from db.models import AuditLog, LLMCallLog, utcnow


# --------------------------------------------------------------------------- #
# Fix 1: file permissions
# --------------------------------------------------------------------------- #

def test_restrict_file_permissions_posix(tmp_path, monkeypatch):
    import utils.file_perms as fp

    f = tmp_path / "secret.session"
    f.write_text("data")
    # Мокаем платформенную ветку через _is_windows (а не глобальный os.name,
    # который ломает pathlib на Windows) и перехватываем chmod.
    monkeypatch.setattr(fp, "_is_windows", lambda: False)
    chmod_calls = []
    monkeypatch.setattr(fp.os, "chmod", lambda p, m: chmod_calls.append((str(p), m)))

    assert fp.restrict_file_permissions(f) is True
    assert chmod_calls and chmod_calls[0][1] == 0o600


def test_restrict_file_permissions_windows_noop(tmp_path, monkeypatch):
    import utils.file_perms as fp

    f = tmp_path / "secret.session"
    f.write_text("data")
    monkeypatch.setattr(fp, "_is_windows", lambda: True)
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setenv("USERDOMAIN", "DOMAIN")
    calls = []
    monkeypatch.setattr(
        fp.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args),
    )
    assert fp.restrict_file_permissions(f) is True
    assert calls and calls[0][0] == "icacls"


def test_restrict_file_permissions_missing(tmp_path, monkeypatch):
    import utils.file_perms as fp

    monkeypatch.setattr(fp, "_is_windows", lambda: False)
    assert fp.restrict_file_permissions(tmp_path / "nope.session") is False


def test_restrict_sqlite_permissions_calls_chmod(monkeypatch):
    import utils.file_perms as fp

    called = []
    monkeypatch.setattr(fp, "restrict_file_permissions", lambda p, mode=0o600: called.append(str(p)) or True)
    fp.restrict_sqlite_permissions("sqlite+aiosqlite:///./sales_agent.db")
    assert any("sales_agent.db" in c for c in called)


# --------------------------------------------------------------------------- #
# Fix 2: Sentry scrubbing
# --------------------------------------------------------------------------- #

def test_sentry_scrub_removes_secrets(monkeypatch):
    import utils.sentry as s

    monkeypatch.setattr(s.settings, "bot_token", "TOKEN123456")
    monkeypatch.setattr(s.settings, "llm_api_key", "sk-secretkey-abcdef")
    monkeypatch.setattr(s.settings, "anthropic_api_key", "")
    monkeypatch.setattr(s.settings, "chat_monitor_api_hash", "")
    monkeypatch.setattr(s.settings, "chat_monitor_phone", "")

    event = {
        "message": "error with TOKEN123456 inside",
        "extra": {"key": "sk-secretkey-abcdef"},
    }
    scrubbed = s._scrub_secrets(event, None)
    assert "TOKEN123456" not in str(scrubbed)
    assert "sk-secretkey-abcdef" not in str(scrubbed)
    assert "***REDACTED***" in str(scrubbed)


def test_sentry_scrub_no_secrets_returns_event(monkeypatch):
    import utils.sentry as s

    for attr in ("bot_token", "llm_api_key", "anthropic_api_key", "chat_monitor_api_hash", "chat_monitor_phone"):
        monkeypatch.setattr(s.settings, attr, "")
    event = {"message": "plain"}
    assert s._scrub_secrets(event, None) == event


# --------------------------------------------------------------------------- #
# Fix 3: Prompt-injection щит
# --------------------------------------------------------------------------- #

def test_wrap_untrusted_escapes_markers():
    from services.ai import _UNTRUSTED_CLOSE, _UNTRUSTED_OPEN, _wrap_untrusted

    payload = f"text {_UNTRUSTED_CLOSE} INJECTION {_UNTRUSTED_OPEN}"
    wrapped = _wrap_untrusted(payload)
    # Внутренние маркеры вырезаны, блок не «закрывается» раньше времени
    assert wrapped.count(_UNTRUSTED_OPEN) == 1
    assert wrapped.count(_UNTRUSTED_CLOSE) == 1


def test_analyze_prompt_has_shield():
    import asyncio

    from services.ai import LLMClient, _INJECTION_SHIELD, _UNTRUSTED_OPEN

    captured = {}

    class _C(LLMClient):
        async def _complete(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"score": 50, "weaknesses": [], "offer": "x", "has_online_booking": null}'

    asyncio.run(_C().analyze_company("Evil Corp"))
    assert _INJECTION_SHIELD in captured["prompt"]
    assert _UNTRUSTED_OPEN in captured["prompt"]


# --------------------------------------------------------------------------- #
# Fix 4: Retention
# --------------------------------------------------------------------------- #

async def test_purge_old_llm_calls(session):
    from services.retention import purge_old_llm_calls

    session.add(LLMCallLog(created_at=utcnow() - timedelta(days=40)))
    session.add(LLMCallLog(created_at=utcnow()))
    await session.commit()

    removed = await purge_old_llm_calls(session, days=30)
    assert removed == 1
    remaining = (await session.execute(select(LLMCallLog))).scalars().all()
    assert len(remaining) == 1


async def test_purge_old_audit_log(session):
    from services.retention import purge_old_audit_log

    session.add(AuditLog(owner_tg_id=1, action="old", created_at=utcnow() - timedelta(days=100)))
    session.add(AuditLog(owner_tg_id=1, action="new", created_at=utcnow()))
    await session.commit()

    removed = await purge_old_audit_log(session, days=90)
    assert removed == 1


async def test_purge_old_chat_message_text(session):
    from services.retention import purge_old_chat_message_text

    old = await repo.create_chat_lead(
        session, owner_tg_id=1, source_chat="c", user_id=1, username="u",
        message_text="старый текст", message_date=utcnow() - timedelta(days=40),
        relevance_score=0.9, llm_reasoning="r",
        message_id=201,
    )
    fresh = await repo.create_chat_lead(
        session, owner_tg_id=1, source_chat="c", user_id=2, username="u2",
        message_text="свежий текст", message_date=utcnow(),
        relevance_score=0.9, llm_reasoning="r",
        message_id=202,
    )
    removed = await purge_old_chat_message_text(session, days=30)
    assert removed == 1
    await session.refresh(old)
    await session.refresh(fresh)
    assert old.message_text is None       # обезличен
    assert fresh.message_text == "свежий текст"  # свежий сохранён


async def test_purge_deleted_leads(session):
    from services.retention import purge_deleted_leads

    lead = await repo.create_lead(session, 1, "Deleted Co")
    lead.deleted_at = utcnow() - timedelta(days=40)
    await session.commit()
    keep = await repo.create_lead(session, 1, "Keep Co")

    removed = await purge_deleted_leads(session, days=30)
    assert removed == 1
    assert await repo.get_lead(session, keep.id, 1) is not None


async def test_run_retention_cleanup(session_factory, monkeypatch):
    import utils.crypto as crypto
    from services.retention import run_retention_cleanup

    monkeypatch.setattr(crypto.settings, "pii_encryption_key", "")
    crypto.reset_fernet()
    counts = await run_retention_cleanup(session_factory)
    assert set(counts) == {
        "llm_call_log", "audit_log", "message_text", "deleted_leads", "chat_inbox"
    }


# --------------------------------------------------------------------------- #
# Fix 5: Шифрование ПДн
# --------------------------------------------------------------------------- #

def test_encrypt_decrypt_roundtrip(monkeypatch):
    from cryptography.fernet import Fernet

    import utils.crypto as crypto

    monkeypatch.setattr(crypto.settings, "pii_encryption_key", Fernet.generate_key().decode())
    crypto.reset_fernet()

    secret = "текст личного сообщения"
    enc = crypto.encrypt_value(secret)
    assert enc != secret
    assert enc.startswith(crypto.ENC_PREFIX)
    assert crypto.decrypt_value(enc) == secret


def test_encrypt_disabled_without_key(monkeypatch):
    import utils.crypto as crypto

    monkeypatch.setattr(crypto.settings, "pii_encryption_key", "")
    crypto.reset_fernet()
    assert crypto.encrypt_value("plain") == "plain"
    assert crypto.decrypt_value("plain") == "plain"


def test_decrypt_plaintext_passthrough(monkeypatch):
    """Старые незашифрованные строки читаются после включения ключа."""
    from cryptography.fernet import Fernet

    import utils.crypto as crypto

    monkeypatch.setattr(crypto.settings, "pii_encryption_key", Fernet.generate_key().decode())
    crypto.reset_fernet()
    assert crypto.decrypt_value("старое plaintext значение") == "старое plaintext значение"


async def test_message_text_encrypted_in_db(session, monkeypatch):
    """ORM читает plaintext, а в сырой БД лежит шифротекст."""
    from cryptography.fernet import Fernet

    import utils.crypto as crypto

    monkeypatch.setattr(crypto.settings, "pii_encryption_key", Fernet.generate_key().decode())
    crypto.reset_fernet()

    lead = await repo.create_chat_lead(
        session, owner_tg_id=1, source_chat="c", user_id=1, username="u",
        message_text="секретный текст", message_date=utcnow(),
        relevance_score=0.9, llm_reasoning="r",
        message_id=203,
    )
    assert lead.message_text == "секретный текст"  # ORM — plaintext

    raw = (await session.execute(
        text("SELECT message_text FROM leads WHERE id = :i"), {"i": lead.id}
    )).scalar_one()
    assert raw.startswith(crypto.ENC_PREFIX)  # в БД — шифротекст


async def test_delete_lead_clears_message_text(session):
    lead = await repo.create_chat_lead(
        session, owner_tg_id=1, source_chat="c", user_id=1, username="u",
        message_text="пдн", message_date=utcnow(), relevance_score=0.9, llm_reasoning="r",
        message_id=204,
    )
    await repo.delete_lead(session, lead.id, 1)
    restored = await repo.get_lead(session, lead.id, 1, include_deleted=True)
    assert restored.message_text is None


# --------------------------------------------------------------------------- #
# Fix 6: Fail-closed allowlist
# --------------------------------------------------------------------------- #

def test_production_empty_allowlist_is_error(monkeypatch):
    from utils.config_validator import validate_config

    class _S:
        bot_token = "x"
        llm_ready = True
        db_url = "sqlite+aiosqlite:///:memory:"
        backup_dir = "backups"
        allowed_user_set = set()
        allowed_user_ids = ""
        llm_daily_limit = 0
        reminders_poll_interval = 60
        environment = "production"

    errors = validate_config(_S())
    assert any("ALLOWED_USER_IDS" in e for e in errors)


def test_development_empty_allowlist_is_warning(monkeypatch):
    from utils.config_validator import validate_config

    class _S:
        bot_token = "x"
        llm_ready = True
        db_url = "sqlite+aiosqlite:///:memory:"
        backup_dir = "backups"
        allowed_user_set = set()
        allowed_user_ids = ""
        llm_daily_limit = 0
        reminders_poll_interval = 60
        environment = "development"

    errors = validate_config(_S())
    assert not any("ALLOWED_USER_IDS" in e for e in errors)


def test_invalid_pii_key_is_config_error(tmp_path):
    from utils.config_validator import validate_config

    class _S:
        bot_token = "x"
        llm_ready = True
        llm_provider = "openrouter"
        db_url = "sqlite+aiosqlite:///:memory:"
        backup_dir = str(tmp_path / "backups")
        allowed_user_set = {1}
        allowed_user_ids = "1"
        llm_daily_limit = 0
        reminders_poll_interval = 60
        environment = "production"
        pii_encryption_key = "not-a-fernet-key"

    errors = validate_config(_S())
    assert any("PII_ENCRYPTION_KEY" in e for e in errors)


def test_negative_security_limits_are_config_errors(tmp_path):
    from utils.config_validator import validate_config

    class _S:
        bot_token = "x"
        llm_ready = True
        llm_provider = "openrouter"
        db_url = "sqlite+aiosqlite:///:memory:"
        backup_dir = str(tmp_path / "backups")
        allowed_user_set = {1}
        allowed_user_ids = "1"
        llm_daily_limit = 0
        reminders_poll_interval = 60
        environment = "production"
        pii_encryption_key = ""
        user_global_rate_limit_seconds = -1
        max_active_reminders_per_user = -1

    errors = validate_config(_S())
    assert any("USER_GLOBAL_RATE_LIMIT_SECONDS" in e for e in errors)
    assert any("MAX_ACTIVE_REMINDERS_PER_USER" in e for e in errors)


# --------------------------------------------------------------------------- #
# CSV formula injection
# --------------------------------------------------------------------------- #

def test_csv_safe_escapes_formula_prefixes():
    from handlers.crm import _csv_safe

    assert _csv_safe("=cmd|'/c calc'!A1").startswith("'")
    assert _csv_safe("+1+1").startswith("'")
    assert _csv_safe("-2+3").startswith("'")
    assert _csv_safe("@SUM(1)").startswith("'")
    assert _csv_safe("normal") == "normal"
    assert _csv_safe(None) == ""
    assert _csv_safe("Компания") == "Компания"
