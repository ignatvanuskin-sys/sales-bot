"""Валидация конфигурации перед стартом бота (CONFIG-2).

Возвращает список строк с ошибками. Пустой список = конфиг валиден.
Предупреждения логируются, но не блокируют старт.
"""

import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def validate_config(settings) -> list[str]:
    """Проверяет критичные параметры конфига. Возвращает список ошибок (блокируют старт).

    Предупреждения (не блокируют) логируются напрямую.
    """
    errors: list[str] = []

    # --- Обязательные параметры ---
    if not settings.bot_token:
        errors.append(
            "BOT_TOKEN не задан. Скопируй .env.example в .env и впиши токен от @BotFather."
        )

    # --- LLM ---
    if not settings.llm_ready:
        logger.warning(
            "Ключ LLM-провайдера (%s) не задан — AI-анализ и генерация сообщений "
            "работать не будут. Заполни LLM_API_KEY (или ANTHROPIC_API_KEY) в .env.",
            settings.llm_provider,
        )

    # --- БД ---
    db_url = settings.db_url
    if db_url.startswith("sqlite"):
        # Проверяем что директория для файла БД существует или может быть создана
        db_path_str = db_url.split("///")[-1]
        if db_path_str and db_path_str not in (":memory:", ""):
            db_dir = Path(db_path_str).parent
            if not db_dir.exists():
                try:
                    db_dir.mkdir(parents=True, exist_ok=True)
                    logger.info("Создана директория для БД: %s", db_dir)
                except OSError as exc:
                    errors.append(f"Не удалось создать директорию для БД {db_dir}: {exc}")

    # --- Бэкап ---
    backup_dir = Path(settings.backup_dir)
    if not backup_dir.exists():
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Создана директория для бэкапов: %s", backup_dir)
        except OSError as exc:
            logger.warning("Не удалось создать директорию для бэкапов %s: %s", backup_dir, exc)

    # --- Allowlist ---
    is_production = getattr(settings, "environment", "development").lower() == "production"
    if not settings.allowed_user_set:
        if is_production:
            # SEC-FIX-6: fail-closed — в production бот без allowlist не стартует
            errors.append(
                "ENVIRONMENT=production, но ALLOWED_USER_IDS пуст — бот был бы доступен "
                "любому пользователю Telegram. Задай ALLOWED_USER_IDS в .env "
                "(свой ID: @userinfobot) или верни ENVIRONMENT=development."
            )

    if is_production:
        rotated_at = getattr(settings, "secrets_rotated_at", "")
        if not rotated_at:
            errors.append(
                "ENVIRONMENT=production требует SECRETS_ROTATED_AT после ротации "
                "BOT_TOKEN, LLM key и Telethon sessions."
            )
        else:
            try:
                datetime.fromisoformat(rotated_at.replace("Z", "+00:00"))
            except ValueError:
                errors.append("SECRETS_ROTATED_AT должен быть ISO-8601 timestamp.")
        if not settings.db_url.startswith("postgresql"):
            errors.append(
                "ENVIRONMENT=production требует PostgreSQL: "
                "задай DB_URL=postgresql+asyncpg://..."
            )
        if not getattr(settings, "redis_url", ""):
            errors.append("ENVIRONMENT=production требует REDIS_URL для shared FSM/locks/rate limits.")
        if settings.llm_daily_limit <= 0:
            errors.append("ENVIRONMENT=production требует LLM_DAILY_LIMIT > 0.")
        if not getattr(settings, "pii_encryption_key", ""):
            errors.append("ENVIRONMENT=production требует PII_ENCRYPTION_KEY.")
        if not getattr(settings, "backup_encryption_key", ""):
            errors.append("ENVIRONMENT=production требует BACKUP_ENCRYPTION_KEY.")
        elif settings.backup_encryption_key == settings.pii_encryption_key:
            errors.append("BACKUP_ENCRYPTION_KEY должен отличаться от PII_ENCRYPTION_KEY.")
        if getattr(settings, "sentry_dsn", "") and not getattr(settings, "sentry_user_hash_salt", ""):
            errors.append("SENTRY_USER_HASH_SALT обязателен при включённом Sentry в production.")
        if getattr(settings, "auto_create_schema", True):
            errors.append(
                "ENVIRONMENT=production требует AUTO_CREATE_SCHEMA=false; "
                "выполни `alembic upgrade head` перед запуском."
            )
        if getattr(settings, "llm_provider", "anthropic").lower() != "anthropic":
            parsed_llm_url = urlsplit(getattr(settings, "llm_base_url", ""))
            allowed_hosts = {
                host.strip().lower()
                for host in getattr(settings, "llm_allowed_hosts", "").split(",")
                if host.strip()
            }
            if parsed_llm_url.scheme != "https" or not parsed_llm_url.hostname:
                errors.append("Production LLM_BASE_URL должен быть валидным HTTPS URL.")
            elif parsed_llm_url.hostname.lower() not in allowed_hosts:
                errors.append(
                    f"LLM_BASE_URL host {parsed_llm_url.hostname!r} не входит в LLM_ALLOWED_HOSTS."
                )

    # --- Проверка корректности ALLOWED_USER_IDS (нечисловые значения) ---
    if settings.allowed_user_ids.strip():
        for raw in settings.allowed_user_ids.split(","):
            value = raw.strip()
            if value and not value.lstrip("-").isdigit():
                logger.warning(
                    "ALLOWED_USER_IDS содержит нечисловое значение %r — оно будет проигнорировано.",
                    value,
                )

    # --- LLM daily limit ---
    if settings.llm_daily_limit < 0:
        errors.append(f"LLM_DAILY_LIMIT должен быть >= 0, получено: {settings.llm_daily_limit}")

    # --- Security limits ---
    global_rate_limit = getattr(settings, "user_global_rate_limit_seconds", 0.5)
    if global_rate_limit < 0:
        errors.append(
            "USER_GLOBAL_RATE_LIMIT_SECONDS должен быть >= 0, "
            f"получено: {global_rate_limit}"
        )
    reminder_limit = getattr(settings, "max_active_reminders_per_user", 100)
    if reminder_limit < 0:
        errors.append(
            "MAX_ACTIVE_REMINDERS_PER_USER должен быть >= 0, "
            f"получено: {reminder_limit}"
        )
    if getattr(settings, "chat_monitor_queue_size", 200) <= 0:
        errors.append("CHAT_MONITOR_QUEUE_SIZE должен быть > 0.")
    if getattr(settings, "chat_monitor_worker_count", 2) <= 0:
        errors.append("CHAT_MONITOR_WORKER_COUNT должен быть > 0.")
    if getattr(settings, "chat_monitor_max_chats", 100) <= 0:
        errors.append("CHAT_MONITOR_MAX_CHATS должен быть > 0.")
    if getattr(settings, "reminders_batch_size", 100) <= 0:
        errors.append("REMINDERS_BATCH_SIZE должен быть > 0.")
    if getattr(settings, "llm_retry_attempts", 2) < 0:
        errors.append("LLM_RETRY_ATTEMPTS должен быть >= 0.")
    if getattr(settings, "llm_retry_base_delay_seconds", 1.0) < 0:
        errors.append("LLM_RETRY_BASE_DELAY_SECONDS должен быть >= 0.")
    if getattr(settings, "external_retry_attempts", 2) < 0:
        errors.append("EXTERNAL_RETRY_ATTEMPTS должен быть >= 0.")
    if getattr(settings, "external_retry_base_delay_seconds", 0.5) < 0:
        errors.append("EXTERNAL_RETRY_BASE_DELAY_SECONDS должен быть >= 0.")

    # --- PII encryption key ---
    pii_key = getattr(settings, "pii_encryption_key", "")
    if pii_key:
        try:
            from cryptography.fernet import Fernet

            Fernet(pii_key.encode())
        except Exception:
            errors.append(
                "PII_ENCRYPTION_KEY невалиден. Сгенерируй новый ключ командой: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
    backup_key = getattr(settings, "backup_encryption_key", "")
    if backup_key:
        try:
            from utils.backup_crypto import _decode_key

            _decode_key(backup_key)
        except Exception:
            errors.append(
                "BACKUP_ENCRYPTION_KEY невалиден. Используй отдельный Fernet-compatible key."
            )

    # --- Retention values ---
    retention_names = (
        "retention_llm_call_log_days",
        "retention_audit_log_days",
        "retention_chat_message_text_days",
        "retention_deleted_lead_days",
        "retention_cleanup_interval_seconds",
    )
    for name in retention_names:
        value = getattr(settings, name, 1)
        if value <= 0:
            errors.append(f"{name.upper()} должен быть > 0, получено: {value}")

    # --- Reminders poll interval ---
    if settings.reminders_poll_interval < 10:
        logger.warning(
            "REMINDERS_POLL_INTERVAL=%s слишком мал (< 10 сек) — возможна высокая нагрузка на БД.",
            settings.reminders_poll_interval,
        )

    return errors
