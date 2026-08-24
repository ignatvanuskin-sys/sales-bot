"""Healthcheck для Docker/docker-compose: проверяет, что бот жив и ready.

Проверка:
1. BOT_TOKEN задан и не пуст — без него бот не стартует.
2. BOT_TOKEN валиден (Bot API getMe) — токен не отозван.
3. База данных доступна и схема готова.
4. Redis доступен, если он обязателен текущим конфигом.

Коды выхода:
  0 — всё ОК (healthcheck passed)
  1 — бот не может работать (healthcheck failed)

Запуск из Dockerfile:
  HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
      CMD python /app/scripts/healthcheck.py

Может запускаться и отдельно (без полного стека):
  python scripts/healthcheck.py
"""

import http.client
import json
import os
import sys
from urllib.parse import urlsplit
from pathlib import Path

# Разрешаем запуск из корня проекта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _get_bot_token() -> str:
    """Пытается прочитать BOT_TOKEN из .env через pydantic или напрямую из os.environ."""
    try:
        from config import settings
        return settings.bot_token
    except Exception:
        pass
    return os.environ.get("BOT_TOKEN", "")


def _mask_db_url(db_url: str) -> str:
    try:
        parts = urlsplit(db_url)
    except Exception:
        return "<invalid-db-url>"
    if not parts.scheme:
        return "<missing-db-url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    path = parts.path or ""
    return f"{parts.scheme}://{netloc}{path}"


def check_bot_token() -> bool:
    """Проверяет, что BOT_TOKEN задан и не пуст (обязательное условие)."""
    token = _get_bot_token()
    if not token:
        print("HEALTHCHECK FAIL: BOT_TOKEN не задан или пуст.")
        return False
    return True


def check_telegram_api() -> bool:
    """Проверяет, что Bot API getMe отвечает (бот может общаться с Telegram)."""
    token = _get_bot_token()
    if not token:
        return False

    try:
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=10)
        conn.request("GET", f"/bot{token}/getMe")
        resp = conn.getresponse()
        if resp.status != 200:
            print(f"HEALTHCHECK FAIL: Bot API вернул HTTP {resp.status}")
            return False
        data = json.loads(resp.read().decode())
        if not data.get("ok"):
            print(f"HEALTHCHECK FAIL: Bot API ответил ok=false: {data}")
            return False
        print(f"HEALTHCHECK OK: Bot API user={data.get('result', {}).get('username', '?')}")
        return True
    except Exception as exc:
        print(f"HEALTHCHECK FAIL: Bot API недоступен: {exc}")
        return False


def check_db_file() -> bool:
    """Лёгкая проверка локального SQLite-файла, если он используется."""
    db_url = os.environ.get(
        "DB_URL",
        os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./sales_agent.db"),
    )
    if not db_url.startswith("sqlite"):
        return True
    db_path = db_url.split("///")[-1] if "///" in db_url else ""
    if not db_path:
        print("HEALTHCHECK WARN: Путь к SQLite БД не определён.")
        return True
    p = Path(db_path)
    if not p.exists():
        print(f"HEALTHCHECK WARN: Файл БД {db_path} ещё не создан (первый запуск).")
        return True
    try:
        with open(p, "ab"):
            pass
        return True
    except OSError as exc:
        print(f"HEALTHCHECK FAIL: Нет доступа на запись к {db_path}: {exc}")
        return False


def check_database_ready() -> bool:
    """Проверяет подключение к БД и готовность схемы."""
    try:
        import asyncio

        from config import settings
        from db.base import verify_schema

        asyncio.run(verify_schema())
        print(f"HEALTHCHECK OK: DB schema ready ({_mask_db_url(settings.db_url)})")
        return True
    except Exception as exc:
        print(f"HEALTHCHECK FAIL: DB/schema not ready: {type(exc).__name__}: {exc}")
        return False


def check_redis_ready() -> bool:
    """Проверяет Redis, если он обязателен по текущему конфигу."""
    try:
        import asyncio

        from config import settings
        from utils.redis_client import get_redis

        async def _check() -> bool:
            redis = await get_redis()
            if not settings.redis_url:
                return True
            return bool(redis is not None and await redis.ping())

        if asyncio.run(_check()):
            return True
        print("HEALTHCHECK FAIL: Redis required but unavailable.")
        return False
    except Exception as exc:
        print(f"HEALTHCHECK FAIL: Redis check failed: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    checks = [
        ("BOT_TOKEN", check_bot_token),
        ("Telegram API", check_telegram_api),
        ("DB файл", check_db_file),
        ("DB/schema", check_database_ready),
        ("Redis", check_redis_ready),
    ]

    all_passed = True
    for name, fn in checks:
        if not fn():
            all_passed = False
            print(f"  [FAIL] {name}")
        else:
            print(f"  [OK] {name}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
