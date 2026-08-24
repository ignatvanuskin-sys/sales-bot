"""P-4: Проверка heartbeat Chat Monitor.

Запускается по cron (раз в час). Читает heartbeat-файл, и если timestamp
старше порога (по умолчанию 30 минут), шлёт уведомление через Bot API.

Запуск:
    python -m scripts.check_heartbeat
    python -m scripts.check_heartbeat --stale-minutes 60

Cron:
    0 * * * * cd /path/to/project && venv/bin/python -m scripts.check_heartbeat
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from utils.bot_api import send_bot_message


async def check_heartbeat(stale_minutes: int = 30) -> bool:
    """Возвращает True если heartbeat свежий, False если устарел/отсутствует."""
    hb_path = Path(settings.heartbeat_file)

    if not hb_path.exists():
        text = (
            "⚠️ Chat Monitor: heartbeat-файл не найден.\n"
            f"Возможно, монитор не запущен или ещё не написал первый heartbeat.\n"
            f"Файл: {hb_path}"
        )
        print(text)
        await send_bot_message(
            settings.bot_token,
            settings.chat_monitor_owner_tg_id,
            text,
        )
        return False

    content = ""
    try:
        content = hb_path.read_text(encoding="utf-8").strip()
        # Current runner writes a JSON payload with metrics; keep compatibility
        # with the legacy plain ISO timestamp format.
        try:
            import json as _json

            payload = _json.loads(content)
        except Exception:
            payload = None
        timestamp = payload.get("ts") if isinstance(payload, dict) else content
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError("heartbeat timestamp is missing")
        last_hb = datetime.fromisoformat(timestamp)
    except (ValueError, OSError) as exc:
        text = (
            "⚠️ Chat Monitor: heartbeat-файл повреждён.\n"
            f"Ошибка: {exc}\n"
            f"Содержимое: {content[:100]!r}"
        )
        print(text)
        await send_bot_message(
            settings.bot_token,
            settings.chat_monitor_owner_tg_id,
            text,
        )
        return False

    now = datetime.now(timezone.utc)
    # heartbeat может быть записан с tzinfo или без (зависит от версии)
    if last_hb.tzinfo is None:
        last_hb = last_hb.replace(tzinfo=timezone.utc)

    age_minutes = (now - last_hb).total_seconds() / 60

    if age_minutes > stale_minutes:
        text = (
            f"🔴 Chat Monitor: нет heartbeat уже {age_minutes:.0f} минут!\n"
            f"Последний heartbeat: {last_hb.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Порог: {stale_minutes} минут.\n"
            f"Возможно, монитор завис или упал."
        )
        print(text)
        await send_bot_message(
            settings.bot_token,
            settings.chat_monitor_owner_tg_id,
            text,
        )
        return False

    print(f"OK: heartbeat is fresh ({age_minutes:.1f} min ago)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Chat Monitor heartbeat")
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=30,
        help="Alert if heartbeat is older than N minutes (default: 30)",
    )
    args = parser.parse_args()
    asyncio.run(check_heartbeat(args.stale_minutes))


if __name__ == "__main__":
    main()
