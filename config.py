"""Конфигурация приложения через pydantic-settings.

ВАЖНО: bot_token намеренно имеет дефолт "" — иначе импорт конфига
(например, в тестах) падает без .env. Проверка на пустой токен
выполняется в bot.py перед стартом polling.

LLM-провайдер переключается через .env (LLM_PROVIDER):
  * "anthropic"  — Anthropic Claude SDK (платный, был исходным);
  * "openrouter" — любой OpenAI-совместимый эндпоинт (OpenRouter, Moonshot,
    локальный сервер и т.п.). Модель/URL/ключ задаются LLM_* переменными.
Anthropic остаётся доступным всегда — это настройка, а не необратимая миграция.
"""

from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    port: int = 0

    # --- SEC-FIX-6: Окружение (development / production) ---
    # В production пустой ALLOWED_USER_IDS становится ОШИБКОЙ запуска (fail-closed):
    # бот без allowlist доступен любому пользователю Telegram.
    environment: str = "development"
    # ISO-8601 timestamp после фактической ротации BOT_TOKEN, LLM key и
    # Telethon sessions. Обязателен в production после security incident.
    secrets_rotated_at: str = ""

    # --- Выбор LLM-провайдера ---
    # "anthropic" | "openrouter" (или любой другой OpenAI-совместимый провайдер)
    llm_provider: str = "anthropic"
    # Имя модели у выбранного провайдера. Для openrouter, напр. moonshotai/kimi-k2.6:free.
    # Намеренно без дефолта-хардкода конкретной модели — задаётся в .env.
    llm_model: str = ""
    # base_url для OpenAI-совместимого провайдера (для anthropic не используется).
    llm_base_url: str = "https://openrouter.ai/api/v1"
    # Comma-separated HTTPS hosts permitted for OpenAI-compatible providers in production.
    llm_allowed_hosts: str = "openrouter.ai,api.openai.com"
    # Ключ выбранного провайдера. Для anthropic, если пусто, берётся ANTHROPIC_API_KEY.
    llm_api_key: str = ""
    # Таймаут одного запроса к LLM, секунды. Бесплатные модели отвечают
    # медленнее — поэтому значение настраиваемое, а не хардкод на оба провайдера.
    llm_timeout_seconds: float = 60.0
    llm_retry_attempts: int = 2
    llm_retry_base_delay_seconds: float = 1.0

    # --- Legacy Anthropic (используются, когда llm_provider=anthropic) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    db_url: str = "sqlite+aiosqlite:///./sales_agent.db"
    # Dev: create_all + lightweight SQLite upgrades. Production: False, схема
    # накатывается только `alembic upgrade head`, бот лишь проверяет её наличие.
    auto_create_schema: bool = True
    
    # --- SECURITY-6: Redis для FSM storage (опционально) ---
    # Если задан — FSM состояния сохраняются между рестартами.
    # Формат: redis://localhost:6379/0 или redis://user:pass@host:port/db
    redis_url: str = ""

    # Интервал фонового поллера напоминаний, секунды
    reminders_poll_interval: int = 60

    # --- Chat Lead Monitor (Telethon userbot) ---
    # Лиды из чатов должны попадать в CRM конкретного владельца бота.
    chat_monitor_owner_tg_id: int = 0
    chat_monitor_api_id: int = 0
    chat_monitor_api_hash: str = ""
    chat_monitor_phone: str = ""
    chat_monitor_session_path: str = "chat_monitor.session"
    # StringSession string for Railway/production (fits in env var, 353 chars, vs 307k file).
    # If set, it takes precedence over the file. Generate via the gen_string_session script.
    chat_monitor_session_string: str = ""
    # Если Telegram не присылает код в приложение, можно попробовать принудительный SMS.
    chat_monitor_force_sms: bool = False
    # Comma-separated usernames/ids: @chat_one,-1001234567890
    chat_monitor_chats: str = ""
    chat_monitor_min_score: float = 0.7
    # --- CHATMON-1: Cooldown на автора, секунды (0 = отключён) ---
    # Один автор может «съесть» весь LLM-бюджет флудом сообщений с ключевыми
    # словами. После скоринга сообщения автора следующие его сообщения
    # пропускаются в течение cooldown (по умолчанию час).
    chat_monitor_author_cooldown_seconds: int = 3600
    chat_monitor_queue_size: int = 200
    chat_monitor_worker_count: int = 2
    chat_monitor_max_chats: int = 100

    # --- P-1: Allowlist пользователей бота ---
    # Comma-separated Telegram user IDs. Пусто = разрешены все (для личного бота).
    allowed_user_ids: str = ""

    # --- P-2: Дневной лимит LLM-вызовов (0 = без лимита) ---
    llm_daily_limit: int = 0

    # --- SEC-FIX: общий rate limit всех update-типов на пользователя ---
    # Старый раздельный лимит message/callback можно было обходить чередованием
    # типов. Глобальный bucket режет суммарную частоту любых апдейтов.
    user_global_rate_limit_seconds: float = 0.5

    # --- SEC-FIX: лимит активных напоминаний на пользователя ---
    max_active_reminders_per_user: int = 100
    reminders_batch_size: int = 100

    # --- PERF-3: Максимум одновременных LLM-вызовов (защита free-tier API от бурстов) ---
    # Бот + chat_monitor могут одновременно инициировать вызовы; семафор
    # ограничивает параллелизм и выстраивает остальных в очередь (backpressure).
    llm_max_concurrency: int = 3

    # --- Overpass backpressure/cache limits ---
    overpass_max_concurrency: int = 3
    overpass_cache_max_entries: int = 500
    overpass_max_response_bytes: int = 5 * 1024 * 1024
    external_retry_attempts: int = 2
    external_retry_base_delay_seconds: float = 0.5

    # --- P-4: Heartbeat-мониторинг Chat Monitor ---
    heartbeat_file: str = "chat_monitor.heartbeat"
    heartbeat_interval_minutes: int = 5

    # --- P-7: Автобэкап БД ---
    backup_dir: str = "backups"
    backup_keep: int = 14
    # Fernet-compatible 32-byte base64 key; используется как AES-256-GCM key
    # для backup-файлов. Должен отличаться от PII_ENCRYPTION_KEY.
    backup_encryption_key: str = ""

    # --- SEC-FIX-5: Шифрование ПДн в БД (Fernet / AES-128-CBC+HMAC) ---
    # Ключ шифрования колонок с ПДн (тексты чатов и др.). Пусто = шифрование
    # ВЫКЛЮЧЕНО (обратная совместимость, данные хранятся открытым текстом).
    # Сгенерировать: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # ВАЖНО: без ключа, которым зашифрованы данные, их НЕ расшифровать —
    # храни ключ отдельно от БД (в .env, не в git). Включение на существующей
    # БД: прозрачная миграция происходит при сохранении/удалении лида (или
    # скриптом scripts/encrypt_existing.py).
    pii_encryption_key: str = ""
    # Сколько дней хранить журнал LLM-вызовов (нужен только для дневного лимита).
    retention_llm_call_log_days: int = 30
    # Сколько дней хранить аудит действий (расследование инцидентов).
    retention_audit_log_days: int = 90
    # Сколько дней хранить текст сообщения из чата (ПДн; лид остаётся, текст удаляется).
    retention_chat_message_text_days: int = 30
    # Сколько дней хранить soft-deleted лиды до окончательного удаления.
    retention_deleted_lead_days: int = 30
    # Интервал фоновой очистки, секунды (по умолчанию раз в сутки).
    retention_cleanup_interval_seconds: int = 86400

    # --- MONITORING-2: Sentry (опционально) ---
    # DSN из настроек проекта на sentry.io. Пусто = Sentry отключён,
    # бот работает как раньше (ошибки только в логах).
    sentry_dsn: str = ""
    # Окружение для группировки событий (production / staging / dev)
    sentry_environment: str = "production"
    # Доля транзакций для performance-мониторинга (0.0 = только ошибки)
    sentry_traces_sample_rate: float = 0.0
    # Отдельная соль для необратимого хеширования Telegram user ID в Sentry.
    sentry_user_hash_salt: str = ""

    # =========================================================================
    # --- Клиентская конфигурация (Production Template) ---
    # Все параметры ниже позволяют адаптировать бота под нового клиента
    # без изменения Python-кода — только через .env.
    # =========================================================================

    # --- ARCH-3: домен 2ГИС (2gis.kz для Казахстана, 2gis.ru для России) ---
    gis_domain: str = "2gis.kz"

    # --- ARCH-2: ниша и ключевые слова Chat Monitor ---
    # Описание ниши для LLM-скоринга (используется в промпте)
    chat_monitor_niche_description: str = "маникюр/ногтевой сервис"
    # Описание целевого лида для LLM (кого ищем)
    chat_monitor_lead_description: str = (
        "соло-мастер маникюра, который публично упоминает запись клиентов, "
        "свободные окна, приём на дому или выезд"
    )
    # Описание нашего продукта/оффера для LLM
    chat_monitor_offer_product: str = "сервис записи клиентов через Telegram-бота"
    # Ключевые слова для первичного фильтра (через запятую).
    # Пусто = использовать встроенные keywords_nail.py
    chat_monitor_keywords: str = ""

    # --- ARCH-1: настройки промптов AI-анализа ---
    # Тип digital-услуг для анализа компании
    ai_service_type: str = "digital-услуги (сайт, онлайн-запись, SMM, реклама)"
    # Наш основной оффер (упоминается в анализе если нет онлайн-записи)
    ai_main_offer: str = "запись через Telegram-бота"
    # Язык ответов бота (используется в промптах генерации сообщений)
    ai_response_language: str = "русском"

    # --- Производные значения (учитывают legacy-переменные) ---

    @property
    def resolved_anthropic_key(self) -> str:
        """Ключ для Anthropic: приоритет у LLM_API_KEY, затем ANTHROPIC_API_KEY."""
        return self.llm_api_key or self.anthropic_api_key

    @property
    def resolved_anthropic_model(self) -> str:
        """Модель для Anthropic: приоритет у LLM_MODEL, затем ANTHROPIC_MODEL."""
        return self.llm_model or self.anthropic_model

    @property
    def llm_ready(self) -> bool:
        """Есть ли ключ, достаточный для работы выбранного провайдера."""
        if self.llm_provider.lower() == "anthropic":
            return bool(self.resolved_anthropic_key)
        return bool(self.llm_api_key)

    @property
    def chat_monitor_chat_list(self) -> list[int | str]:
        """Список чатов для Telethon из CHAT_MONITOR_CHATS.

        Числовые id приводим к int, usernames оставляем строками. Пустые элементы
        игнорируются, чтобы удобно редактировать .env без правки кода.
        """
        chats: list[int | str] = []
        for raw in self.chat_monitor_chats.split(","):
            value = raw.strip()
            if not value:
                continue
            if value.lstrip("-").isdigit():
                chats.append(int(value))
            else:
                chats.append(value)
        return chats

    @cached_property
    def chat_monitor_keywords_list(self) -> tuple[str, ...] | None:
        """Список ключевых слов из конфига. None = использовать встроенные (ARCH-2)."""
        if not self.chat_monitor_keywords.strip():
            return None
        return tuple(
            kw.strip()
            for kw in self.chat_monitor_keywords.split(",")
            if kw.strip()
        )

    @cached_property
    def allowed_user_set(self) -> set[int]:
        """Множество разрешённых Telegram user IDs из ALLOWED_USER_IDS.

        Вычисляется один раз при первом обращении (CONFIG-3).
        Пустое множество = доступ не ограничен (личный бот).
        """
        result: set[int] = set()
        for raw in self.allowed_user_ids.split(","):
            value = raw.strip()
            if value and value.lstrip("-").isdigit():
                result.add(int(value))
        return result

    @property
    def chat_monitor_ready(self) -> bool:
        """Достаточен ли конфиг для запуска Telethon-монитора."""
        return bool(
            self.chat_monitor_owner_tg_id
            and self.chat_monitor_api_id
            and self.chat_monitor_api_hash
            and self.chat_monitor_phone
            and self.chat_monitor_session_path
            and self.chat_monitor_chat_list
        )


settings = Settings()
