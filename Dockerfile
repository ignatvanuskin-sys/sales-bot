FROM python:3.11-slim

# Небуферизованный вывод для логов контейнера; не писать .pyc (код монтируется read-only по смыслу)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# PostgreSQL client нужен scripts.backup_db.py для pg_dump в backup service.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Только production-зависимости (без pytest/ruff/mypy)
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt

# Непривилегированный пользователь — контейнер не должен работать от root.
# python:3.11-slim основан на Debian, useradd доступен.
RUN useradd --create-home --uid 10001 appuser

# Копируем только исходный код (тесты, документация, .git, .env исключены через .dockerignore).
# --chown: appuser должен владеть кодом и рабочей директорией.
COPY --chown=appuser:appuser . .

# Runtime data разделены на volumes: DB/data, Telethon session, backups.
# Создаём заранее и отдаём appuser: пустой named volume при первом запуске
# унаследует владельца и права из образа — бот сможет писать SQLite/бэкапы.
RUN mkdir -p /app/data /app/session /app/backups \
    && chown -R appuser:appuser /app/data /app/session /app/backups

# Данные (БД, сессия Telethon, бэкапы) — на Railway используются Railway Volumes,
# директива VOLUME не поддерживается Railway builder, поэтому убрана.

ENV DB_URL=sqlite+aiosqlite:////app/data/sales_agent.db
ENV CHAT_MONITOR_SESSION_PATH=/app/session/chat_monitor.session
ENV HEARTBEAT_FILE=/app/data/chat_monitor.heartbeat
ENV BACKUP_DIR=/app/backups

# Дальше всё (healthcheck, бот) — от appuser
USER appuser

# Healthcheck: проверяем, что Bot API getMe отвечает (бот жив и может общаться с Telegram)
# Использует scripts/healthcheck.py (без сетевых зависимостей — только стандартная библиотека + http.client).
# Скрипт читает конфиг и открывает файл БД на append — /app/data доступна appuser на запись (см. выше).
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python /app/scripts/healthcheck.py

CMD ["python", "bot.py"]
