# AI Sales Agent — личный Telegram-бот

Поиск потенциальных бизнес-клиентов (OpenStreetMap Overpass API), AI-анализ через Claude,
генерация сообщений для холодного контакта, CRM-лайт со статусами и напоминаниями.

**Статус:** задеплоен на Railway (Postgres + Redis + бот, GitHub auto-deploy). История Git очищена от секретов (filter-repo + force-push). Осталось владельцу: ротировать `BOT_TOKEN`/`LLM_API_KEY`/Telethon session (см. `SECURITY_INCIDENT.md`).

## 🚀 Ключевые возможности

- ✅ **Rate limiting** — защита от флуда и DoS-атак
- ✅ **Redis FSM storage** — сохранение состояний между рестартами
- ✅ **Distributed idempotency** — предотвращение дублей в multi-instance
- ✅ **Global error handler** — graceful degradation при ошибках
- ✅ **Healthcheck** — мониторинг состояния бота и БД
- ✅ **Docker support** — готовая контейнеризация для production
- ✅ **Transaction safety** — атомарные операции с БД
- ✅ **Access control** — allowlist пользователей

## Запуск

### 🐳 Docker (development / staging)

```bash
# 1. Создать .env из примера
cp .env.example .env
# Отредактировать .env - задать BOT_TOKEN и LLM_API_KEY

# 2. Запустить бота
docker-compose up -d bot

# 3. Проверить healthcheck
docker ps  # Должен быть "healthy" в STATUS
docker-compose logs -f bot  # Просмотр логов
```

**Преимущества Docker:**
- Изолированная среда без конфликтов зависимостей
- Автоматический restart при падении (healthcheck)
- Персистентность данных через volumes
- Простой деплой на любом сервере

### 💻 Локальный запуск (development)

Требуется **Python 3.11+**. Запускать бот нужно **строго из venv на 3.11**, а не
из глобального `python` (см. предупреждение ниже — из-за этого уже был реальный
краш несовместимости версий).

```bat
:: 1. venv именно на 3.11 (системный python может быть 3.10 — не подойдёт)
py -3.11 -m venv venv
venv\Scripts\activate

:: 2. Зависимости (в активированный venv) — из ПОЛНОГО lock-файла:
pip install -r requirements-lock.txt
:: requirements-lock.txt пинит всё дерево (в т.ч. транзитивные httpx, pydantic и
:: т.п.) — воспроизводимая установка без риска подтянуть несовместимую версию.
:: requirements.txt — только верхнеуровневые пакеты, для чтения/обновления версий.

:: 3. Конфиг
copy .env.example .env
:: впиши в .env:
::   BOT_TOKEN=...          (от @BotFather)
::   LLM_PROVIDER=openrouter (бесплатно) или anthropic
::   LLM_API_KEY=...         (ключ выбранного провайдера)
::   LLM_MODEL=moonshotai/kimi-k2.6:free   (для openrouter; без лишнего префикса LLM_MODEL=)
:: чтобы вернуться на Anthropic: LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY

:: 4. Старт (таблицы SQLite создадутся автоматически)
python bot.py
```

Linux/macOS: `python3.11 -m venv venv && source venv/bin/activate` вместо первых двух строк.

### Основной способ запуска — `run.bat` / `run.sh`

После разовой установки (шаги 1–3 выше) бот запускается лаунчером — руками
активировать venv и печатать `python bot.py` каждый раз не нужно:

- **Windows:** дважды кликни **`run.bat`** (или запусти из консоли). Он сам
  перейдёт в папку проекта, активирует venv и стартует бота с полными проверками:
  - Проверка существования venv
  - Проверка .env файла
  - Проверка версии Python (рекомендуется 3.11+)
  - Проверка установленных зависимостей
  - Проверка доступности Redis (если настроен)
  - Автоматический запуск Chat Monitor (если настроен в .env)
  
  Если venv ещё нет — покажет понятную ошибку (`[ERROR] venv не найден…`), 
  а не голый traceback.

- **Linux/macOS:** `./run.sh` (при первом запуске один раз: `chmod +x run.sh`).

Остановка — **`Ctrl+C`**: бот и Chat Monitor завершатся штатно с сообщением
`[INFO] … Бот остановлен пользователем (Ctrl+C).` и кодом выхода 0, без пугающего
трейсбека.

> ⚠️ **Запускай только через активированный venv.** Если вызвать `python bot.py`
> без активации (`venv\Scripts\activate`), Python возьмёт **глобальные `--user`
> пакеты**, версии которых могут не совпадать с протестированными в
> `requirements.txt`. Именно так возник краш `AsyncClient.__init__() got an
> unexpected keyword argument 'proxies'`: в проде стоял старый `openai`, не
> совместимый со свежим `httpx`. При старте `bot.py` логирует warning, если
> интерпретатор не 3.11+ или похоже, что запуск идёт не из venv, — но это
> подстраховка, а не замена активации.

### Живая проверка LLM (не мок)

```bat
python scripts\smoke_llm.py
```

Делает один реальный вызов к настроенному в `.env` провайдеру (анализ + генерация)
и печатает сырой ответ модели. Удобно, чтобы отделить проблему конфига/сети/версий
пакетов от логики бота.

### Безопасный live smoke test

Проверяет реальные Telegram Bot API, Overpass, LLM, Redis и CRUD временной БД.
Рабочая CRM по умолчанию не изменяется, сырые ответы модели и секреты не печатаются:

```bat
venv\Scripts\python.exe -m scripts.live_smoke
```

Для проверки без расходов LLM:

```bat
venv\Scripts\python.exe -m scripts.live_smoke --skip-llm
```

Для проверки без Telegram API и Redis:

```bat
venv\Scripts\python.exe -m scripts.live_smoke --skip-telegram --skip-redis
```

Код выхода `0` означает успешное завершение выбранных проверок. Chat Monitor
намеренно не запускается этим скриптом: его авторизация может изменить Telethon
session и потребовать код Telegram. Его следует проверять отдельно после создания
новой session.

### Живой прогон всех функций (не мок)

```bat
python scripts\live_walkthrough.py
```

Прогоняет весь пользовательский путь (старт → меню → поиск → карточка →
AI-анализ → генерация → CRM → напоминания) прямыми вызовами реальных хендлеров,
но **без мокания** Overpass и LLM — реальная сеть. Пишет во временную БД, не
трогая `sales_agent.db`. Ловит баги, которые не видны на моках (напр. города,
где OSM хранит имя в другом регистре/языке). Не часть pytest.

## Тесты

```bash
pytest --cov
```

Тесты не ходят в сеть: Overpass и оба LLM-клиента (Anthropic и OpenAI-совместимый)
замоканы, БД — in-memory SQLite.

## Chat Lead Monitor

Отдельный пассивный источник лидов из открытых Telegram-чатов: Telethon слушает
новые сообщения, проверяет только чаты из настроек, прогоняет их через
keyword-фильтр и LLM-score для nail-ниши, затем сохраняет релевантные сообщения
в общую CRM. Список участников чатов не запрашивается.

> ⚠️ **Безопасность и юридика (обязательно к прочтению):**
> - **Используйте ОТДЕЛЬНЫЙ сервисный аккаунт Telegram, НЕ личный.** Файл
>   `chat_monitor.session` = полный доступ к аккаунту (чтение/отправка от его
>   имени). При компрометации личного аккаунта blast radius — вся цифровая личность.
> - Бот выставляет `chat_monitor.session` права **0600** при старте (Linux).
>   **Не включайте session-файл в бэкапы** и не храните в публичных томах.
> - **Мониторинг чужих сообщений в чатах** — обработка ПДн без согласия участников.
>   Это регулируется 152-ФЗ/GDPR и Telegram ToS (userbot-автоматизация может
>   привести к бану аккаунта). Тексты сообщений шифруются (см. ниже) и
>   автоудаляются по retention — см. раздел «Приватность и защита ПДн».

**✨ Новое в v2.0:** Chat Monitor автоматически запускается вместе с ботом
(если настроен в .env) — не требуется отдельный процесс!

Настройка в `.env` без правки кода:

```env
CHAT_MONITOR_OWNER_TG_ID=123456789
CHAT_MONITOR_API_ID=123456
CHAT_MONITOR_API_HASH=your_api_hash
CHAT_MONITOR_PHONE=+77001234567
CHAT_MONITOR_SESSION_PATH=chat_monitor.session
CHAT_MONITOR_FORCE_SMS=false
CHAT_MONITOR_CHATS=@open_nails_chat,-1001234567890
CHAT_MONITOR_MIN_SCORE=0.7
```

`CHAT_MONITOR_CHATS` и `CHAT_MONITOR_MIN_SCORE` используются как начальные
значения. Дальше управлять ими можно прямо в боте: главное меню ->
`Chat Monitor` -> `Добавить чат` / `Изменить threshold` / `Включить мониторинг`.

Секреты Telethon (`API_ID`, `API_HASH`, `PHONE`, `SESSION_PATH`) намеренно
остаются только в `.env` и не вводятся в Telegram-чат боту. Ключевые слова
nail-фильтра расширяются в `chat_monitor/keywords_nail.py`.

### Автоматический запуск (рекомендуется)

**Chat Monitor автоматически запускается как фоновая задача при запуске бота:**

```bat
rem Windows
run.bat

rem Linux/macOS
./run.sh

rem Или напрямую
python bot.py
```

При первом запуске Telethon попросит код авторизации. Он обычно приходит в
официальный Telegram-клиент на этом номере, а не SMS. Проверь активные
устройства, архивные чаты и `Service Notifications`. Если код не приходит,
поставь `CHAT_MONITOR_FORCE_SMS=true` и перезапусти.

**Логи Chat Monitor** отображаются вместе с логами бота:
```
[INFO] Chat Monitor: starting (config ready)
[INFO] Chat Monitor: authenticated as user_id=123456 username=myuser
[INFO] Background tasks: reminders, chat_monitor
```

**Остановка:** Ctrl+C остановит и бота, и Chat Monitor gracefully.

### Standalone запуск Chat Monitor

В production standalone runner запрещён. Chat Monitor должен запускаться только
внутри `bot.py`, иначе два процесса могут использовать одну Telethon session,
создавать дубли и повреждать session-файл.

При первом запуске Telethon попросит код. Он обычно приходит в официальный
Telegram-клиент на этом номере, а не SMS. Проверь активные устройства, архивные
чаты и `Service Notifications`. Если код не приходит, поставь
`CHAT_MONITOR_FORCE_SMS=true`, подожди несколько минут и запусти runner заново.
После успешного ввода кода будет создан `*.session` файл; он чувствительный и
игнорируется через `.gitignore`.

Если код всё равно не приходит, используй QR-логин без SMS:

```bat
python scripts\telethon_qr_login.py
```

Скрипт создаст и откроет `chat_monitor_qr.png`. Сканируй его из Telegram:
`Settings -> Devices -> Link Desktop Device`. После успешного скана будет
авторизован тот же `CHAT_MONITOR_SESSION_PATH`, и `chat_monitor.runner` запустится
без кода.

## Структура

- `bot.py` — точка входа (polling + фоновый поллер напоминаний)
- `config.py` — настройки из `.env` (pydantic-settings)
- `db/` — модели (User, Lead, Reminder) и CRUD с транзакциями
- `services/` — Overpass-клиент, LLM-провайдер (Anthropic/OpenAI-совместимый: анализ + генерация), поллер напоминаний
- `chat_monitor/` — Telethon-монитор открытых чатов, keyword-фильтр и сохранение chat-лидов в CRM
- `handlers/` — aiogram-роутеры: старт, меню, поиск, анализ, сообщения, CRM
- `keyboards/`, `states/` — inline-клавиатуры и FSM-состояния
- `utils/` — кастомные эмодзи, безопасная отправка, rate limiting, error handling, idempotency
- `tests/` — pytest (344+ тестов; ищет юниты без сети: Overpass/LLM/Redis — моки)
- `scripts/` — утилиты для healthcheck, backup, smoke tests

## 📊 Мониторинг и команды

### Команды бота

- `/start` — открыть главное меню
- `/help` — показать подсказку по возможностям
- `/stats` — статистика лидов, конверсии и AI-расходов
- `/health` — проверка состояния бота (uptime, БД, Redis, LLM)
- `/cancel` — отменить текущий сценарий

### Healthcheck

**Встроенная команда:**
```bash
# В боте отправить /health
```

**Скрипт проверки (без бота):**
```bash
python scripts/healthcheck.py
# Код выхода: 0 = OK, 1 = FAIL
```

**Docker healthcheck (автоматический):**
- Проверяет Bot API каждые 60 сек
- Автоматически перезапускает контейнер при 3 последовательных сбоях
- Статус: `docker ps` (колонка STATUS)

### Логирование

**Локально:**
```bash
python bot.py  # Логи в stdout
```

**Docker:**
```bash
docker-compose logs -f bot           # Следить за логами бота
docker-compose logs --tail=100 bot   # Последние 100 строк
```

**Уровни логирования:**
- `INFO` — нормальная работа (старт, shutdown, отправка напоминаний)
- `WARNING` — не критичные проблемы (Redis недоступен, fallback на memory)
- `ERROR` — ошибки обработки запросов (логируются, но бот продолжает работать)

## 🔐 Безопасность и производительность

### Реализованные защиты

1. **Rate Limiting**
   - 1 сообщение/сек на пользователя
   - 2 callback/сек на пользователя
   - общий bucket `USER_GLOBAL_RATE_LIMIT_SECONDS` режет суммарную частоту
     любых апдейтов (нельзя обойти чередованием message/callback)
   - Защита от флуда и перерасхода LLM-лимита

2. **Access Control**
   ```env
   ALLOWED_USER_IDS=123456789,987654321
   ```
   Пусто = нет ограничений (личный бот)

3. **LLM Budget Control**
    ```env
    LLM_DAILY_LIMIT=100
    ```

4. **Transaction Safety**
   - Все критичные операции в транзакциях
   - BEGIN IMMEDIATE для SQLite (защита от гонок)
   - UNIQUE constraints для дедупликации

5. **Distributed Idempotency**
   - Предотвращение дублей LLM-вызовов
   - Работает в multi-instance через Redis
   - Graceful fallback на memory locks

6. **Global Error Handler**
   - Перехват необработанных исключений
   - User-friendly сообщения
   - Сохранение FSM состояния

### Защита ПДн и мониторинг (SEC-FIX, v2.1)

Добавлено по результатам security-аудита (13 векторов):

7. **Шифрование ПДн в БД (Fernet)**
   ```env
   PII_ENCRYPTION_KEY=<сгенерируй: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
   ```
   Тексты чужих сообщений (`message_text`) хранятся зашифрованными (AES-128-CBC+HMAC).
   Прозрачно для кода: ORM читает/пишет обычные строки, в БД — шифротекст с префиксом
   `enc::v1::`. Без ключа — обратная совместимость (plaintext). Контакты для связи
   (phone/username) осознанно не шифруются — по ним нужен поиск в CRM.
   Для разового шифрования уже существующих plaintext-строк:
   ```bash
   python -m scripts.encrypt_existing        # dry-run
   python -m scripts.encrypt_existing --apply
   ```

8. **Retention (152-ФЗ: не храним дольше необходимого)**
   Фоновая очистка раз в сутки (настраивается `RETENTION_*_DAYS`):
   - тексты чатов → обезличиваются через 30 дней (лид остаётся, текст удаляется);
   - `llm_call_log` → 30 дней, `audit_log` → 90 дней;
   - soft-deleted лиды → окончательно удаляются через 30 дней;
   - при удалении лида ПДн стираются сразу.

9. **Prompt-injection щит**
   Данные из OSM и тексты чатов оборачиваются в маркеры `<UNTRUSTED_DATA>` с
   инструкцией-щитом: модель игнорирует команды внутри них. Маркеры внутри самих
   данных экранируются (нельзя «закрыть» блок раньше времени).

10. **Sentry с scrubbing'ом секретов**
    `before_send` вычищает `BOT_TOKEN`, `LLM_API_KEY`, `API_HASH`, телефон из событий
    (traceback locals, breadcrumbs) перед отправкой.

11. **Fail-closed allowlist в production**
    ```env
    ENVIRONMENT=production  # + пустой ALLOWED_USER_IDS → бот НЕ стартует (ошибка)
    ```

12. **Защита файлов с секретами**
    `chat_monitor.session` и файл SQLite получают права `0600` при старте (Linux).

13. **CSV formula injection — экранирование** полей `= + - @` префиксом `'`.

14. **Лимит активных напоминаний**
    ```env
    MAX_ACTIVE_REMINDERS_PER_USER=100
    ```
    Защита от раздувания БД и DoS фонового поллера.

### Производительность

- **N+1 queries устранены**: `selectinload` для связанных данных
- **Индексы БД**: owner_tg_id, status, chat deduplication
- **Агрегированные запросы**: `/stats` — 3 запроса вместо 5+
- **Connection pooling**: HTTP клиенты переиспользуются

## 🔧 Настройка для production

### Production prerequisites

Production режим (`ENVIRONMENT=production`) теперь fail-closed и не запустится,
пока не выполнены все требования ниже:

- `SECURITY_INCIDENT.md`: ротация `BOT_TOKEN`, LLM key, Telegram sessions и новая Telethon session.
- `SECRETS_ROTATED_AT` с фактическим ISO-8601 временем завершения ротации.
- `DB_URL=postgresql+asyncpg://...`.
- `REDIS_URL=redis://:PASSWORD@redis:6379/0`.
- непустой `ALLOWED_USER_IDS`.
- `LLM_DAILY_LIMIT > 0`.
- `PII_ENCRYPTION_KEY` и отдельный `BACKUP_ENCRYPTION_KEY`.
- `AUTO_CREATE_SCHEMA=false`.
- при OpenAI-compatible провайдере: `LLM_BASE_URL` должен быть HTTPS, а host должен входить в `LLM_ALLOWED_HOSTS`.

Для платформ типа Render Web Service, которые требуют открытый HTTP port,
бот автоматически поднимет минимальный health server на `PORT` и будет отвечать
на `/` и `/healthz` c `{"ok": true}`.

Проверка:

```bash
python -m scripts.check_tracked_secrets
python -m scripts.production_readiness
```

### Redis

```env
REDIS_URL=redis://localhost:6379/0
```

**Development без Redis:**
- FSM состояния теряются при рестарте
- Идемпотентность работает только в single-instance
- LLM-кэш анализа — in-memory (теряется при рестарте)

**С Redis:**
- FSM персистентность
- Distributed locks
- Персистентный LLM-кэш (24ч TTL)
- Горизонтальное масштабирование

### PostgreSQL

```env
DB_URL=postgresql+asyncpg://user:pass@localhost/sales_agent
```

Для `ENVIRONMENT=production` PostgreSQL обязателен.

Минимальная последовательность для production:
```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile postgres up -d postgres redis
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile postgres run --rm migrate
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile postgres run --rm bot python -m scripts.production_readiness
```
В production бот не меняет схему на старте. Схема накатывается только через
Alembic service `migrate` или `alembic upgrade head`. Бэкап PG — через
`scripts/backup_db.py`; при `BACKUP_ENCRYPTION_KEY` сохраняется только AES-GCM
зашифрованный файл. Инцидент с историческим архивом описан в
`SECURITY_INCIDENT.md`.

### Deploy на Railway (рекомендуемый production)

Проект задеплоен на Railway: Postgres + Redis + бот (GitHub auto-deploy из `main`).

1. Создать Postgres и Redis: `railway add --database postgres` / `railway add --database redis`
2. Настроить переменные на сервисе бота (`railway variable set KEY=VAL --service sales-bot`):
   `ENVIRONMENT=production`, `DB_URL=postgresql+asyncpg://...@postgres.railway.internal:5432/railway`,
   `REDIS_URL=redis://...@redis.railway.internal:6379`, `ALLOWED_USER_IDS`, `LLM_DAILY_LIMIT=100`,
   `PII_ENCRYPTION_KEY`/`BACKUP_ENCRYPTION_KEY` (разные Fernet), `SECRETS_ROTATED_AT`,
   `AUTO_CREATE_SCHEMA=false`.
3. Миграции: `railway run --service sales-bot -- alembic upgrade head` (схема → `0006`).
4. Пуш в `main` → Railway авто-деплой. Логи: `railway logs --service sales-bot`.

> ВАЖНО: маппинг `Dockerfile` убран `VOLUME` (Railway не поддерживает) — данные живут
> в Railway Volumes у Postgres/Redis. Публичные TCP-прокси для БД следует удалять после
> проверки: `railway tcp-proxy delete <id> --service postgres --yes`.

### Мониторинг (рекомендуется)

**Sentry** уже интегрирован — достаточно задать DSN в `.env`:
```env
SENTRY_DSN=https://...@o....ingest.sentry.io/...
SENTRY_ENVIRONMENT=production
```
Отправляются необработанные исключения хендлеров, сбои фоновых циклов (напоминания, chat_monitor) и ERROR+ логи — с контекстом tg user_id и FSM-состояния. Без `SENTRY_DSN` всё работает как раньше (no-op).

**Prometheus metrics** (опционально):
- Встроенный `utils/metrics.py` собирает базовые метрики
- Для Prometheus потребуется endpoint и exporter

## 📈 Миграция на новую версию

### Обновление зависимостей

```bash
# Локально
pip install -r requirements-lock.txt

# Docker (пересборка образа)
docker-compose build bot
docker-compose up -d bot
```

### Бэкап БД перед обновлением

```bash
# Ручной бэкап
python scripts/backup_db.py

# Автоматический (через cron или Docker Compose)
docker compose --profile backup run --rm backup
```

### Накат миграций (если есть изменения схемы)

```bash
# Через Alembic (если настроен)
alembic upgrade head

# Production: только Alembic, затем readiness-check
alembic upgrade head
python -m scripts.production_readiness
```

## 🐛 Troubleshooting

### Бот не стартует

1. **Проверить BOT_TOKEN:**
   ```bash
   python scripts/healthcheck.py
   ```

2. **Проверить LLM конфиг:**
   ```bash
   python scripts/smoke_llm.py
   ```

3. **Проверить логи:**
   ```bash
   docker-compose logs bot  # Docker
   python bot.py            # Локально
   ```

### Redis недоступен

- Бот автоматически использует MemoryStorage
- Логирует WARNING при старте
- Проверить: `docker ps | grep redis` или `redis-cli ping`

### FSM состояния теряются

- Проверить REDIS_URL в .env
- Проверить доступность Redis: `/health` команда
- При отсутствии Redis состояния теряются при рестарте (это норма)

### Напоминания не отправляются

- Проверить логи reminders_loop: `docker-compose logs bot | grep reminder`
- Проверить timezone: напоминания работают в UTC
- Проверить БД: `SELECT * FROM reminders WHERE is_sent = 0`

### Rate limit ошибки

- Проверить LLM_DAILY_LIMIT в .env
- Команда `/stats` покажет текущие расходы
- Увеличить лимит или переключиться на другую модель

## 📚 Дополнительная документация

- [FIXES_APPLIED.md](FIXES_APPLIED.md) — детальный список исправленных проблем
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — полный обзор архитектуры
- `.kimchi/docs/audit_report.md` — результаты аудита безопасности

## Структура (обновлено)

## Кастомные премиум-эмодзи

Тексты сообщений используют кастомные эмодзи из пака
[tgmacicons](https://t.me/addemoji/tgmacicons) через HTML-тег
`<tg-emoji emoji-id="...">X</tg-emoji>` (требуется `parse_mode="HTML"` — включён
глобально в `bot.py`).

- `utils/emoji_config.py` — маппинг Unicode → emoji-id, класс `E` (HTML для текстов)
  и класс `P` (обычный юникод для `show_alert`, где HTML не рендерится).
- ID получены через @userinfobot. Несколько эмодзи осознанно делят один ID
  (алиасы одного визуала: 📞/☎️/📱, 📅/🗓 и т.п.) — см. комментарий в файле.
- **В кнопках (`InlineKeyboardButton`) кастомные эмодзи не используются** —
  Telegram не рендерит `<tg-emoji>` в тексте кнопок.

**Если эмодзи перестали отображаться** (пак удалён/ID невалиден): все отправки идут
через `utils/safe_send.py` (`safe_answer` / `safe_edit` / `safe_bot_send`) — при
`TelegramBadRequest` (например `EMOJI_INVALID`) сообщение автоматически
переотправляется с обычными юникод-эмодзи. Пользователь без ответа не останется.
Чтобы обновить ID: добавь пак заново, отправь эмодзи боту @userinfobot и впиши
новые ID в `CUSTOM_EMOJIS`.

## Заметки

- Время напоминаний — UTC.
- Лимит результатов поиска — 60 (MAX_LIMIT), иначе Overpass может вернуть тысячи строк.
- Соцсети (`socials`) почти всегда пусты — OSM их практически не отдаёт.
- Версии в `requirements.txt` — верхнеуровневые пакеты; полный lock всего дерева
  (включая транзитивные) — в `requirements-lock.txt`, ставить прод из него.
  Регенерация после смены версий: `pip install -r requirements.txt && pip freeze
  > requirements-lock.txt` в чистом venv. `pip check` — без конфликтов.
