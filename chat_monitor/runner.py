"""CLI-запуск Telethon userbot для пассивного мониторинга чатов."""

import asyncio
import logging
from pathlib import Path

from config import settings
from chat_monitor.config_store import ChatMonitorConfig, normalize_chat_ref
from db.base import init_db, session_factory
from chat_monitor.processor import candidate_from_event, process_candidate
from db import repo
from services.ai import AIError
from utils.file_perms import restrict_file_permissions
from utils.sentry import capture_exception

logger = logging.getLogger(__name__)


def ensure_session_path_ready() -> None:
    """Ensure the Telethon session parent directory exists and is writable.

    If chat_monitor.session.b64 exists alongside the source code, decodes it
    into the configured .session path at startup. This lets us commit a
    pre-authorized Telethon session as base64 text (safe for git) instead
    of binary, avoiding the need for interactive auth on Render.

    The .b64 file is created locally after QR login:
        python scripts/telethon_qr_login.py
        base64 -w0 chat_monitor.session > chat_monitor.session.b64
    """
    import base64

    session_path = Path(settings.chat_monitor_session_path)
    b64_candidates = [
        session_path.with_suffix(".session.b64"),
        Path("chat_monitor.session.b64"),
    ]
    if not session_path.exists():
        for b64_path in b64_candidates:
            if b64_path.exists():
                try:
                    session_path.parent.mkdir(parents=True, exist_ok=True)
                    encoded = b64_path.read_text().strip()
                    decoded = base64.b64decode(encoded)
                    session_path.write_bytes(decoded)
                    logger.info(
                        "Session decoded from %s -> %s (%d bytes)",
                        b64_path, session_path, len(decoded),
                    )
                    break
                except Exception as exc:
                    logger.warning("Failed to decode session from %s: %s", b64_path, exc)

    candidates = [session_path]

    if session_path.is_absolute() and "tmp" not in {part.lower() for part in session_path.parts}:
        candidates.append(Path("/tmp") / session_path.name)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parent = candidate.parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
            probe = parent if str(parent) not in ("", ".") else Path.cwd()
            if not probe.exists() or not probe.is_dir():
                raise RuntimeError(f"Chat monitor session directory is unavailable: {probe}")
            settings.chat_monitor_session_path = str(candidate)
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(
        f"Chat monitor session path is not writable: {settings.chat_monitor_session_path}"
    ) from last_error


def _ensure_ready() -> None:
    if not (
        settings.chat_monitor_owner_tg_id
        and settings.chat_monitor_api_id
        and settings.chat_monitor_api_hash
        and settings.chat_monitor_phone
        and settings.chat_monitor_session_path
    ):
        raise RuntimeError(
            "Chat monitor is not configured. Set CHAT_MONITOR_OWNER_TG_ID, "
            "CHAT_MONITOR_API_ID, CHAT_MONITOR_API_HASH, CHAT_MONITOR_PHONE, "
            "and CHAT_MONITOR_SESSION_PATH in .env. Chats/threshold can be set in the bot UI."
        )
    if not settings.llm_ready:
        raise RuntimeError("LLM provider is not configured for chat monitor scoring.")


def _env_default_chats() -> list[str]:
    return [str(chat) for chat in settings.chat_monitor_chat_list]


def _mask_phone(phone: str) -> str:
    phone = normalize_phone(phone)
    if len(phone) <= 4:
        return "****"
    return "*" * (len(phone) - 4) + phone[-4:]


def normalize_phone(phone: str) -> str:
    """Telethon надёжнее авторизуется с номером без пробелов/скобок/дефисов."""
    phone = phone.strip()
    if not phone:
        return phone
    prefix = "+" if phone.startswith("+") else ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return prefix + digits


async def load_runtime_config() -> ChatMonitorConfig:
    async with session_factory() as session:
        row = await repo.get_or_create_chat_monitor_settings(
            session,
            settings.chat_monitor_owner_tg_id,
            default_chats=_env_default_chats(),
            default_min_score=settings.chat_monitor_min_score,
        )
        return repo.chat_monitor_settings_to_config(row)


async def event_matches_chat_refs(event, chat_refs: list[str]) -> bool:
    chat_id = getattr(event, "chat_id", None)
    chat = await event.get_chat()
    username = getattr(chat, "username", None)
    username = username.lower() if username else None

    for raw_ref in chat_refs:
        ref = normalize_chat_ref(raw_ref)
        if not ref:
            continue
        if ref.lstrip("-").isdigit():
            if chat_id is not None and int(ref) == int(chat_id):
                return True
            continue
        if username and ref.lstrip("@").lower() == username:
            return True
    return False


async def _heartbeat_loop() -> None:
    """P-4: Периодически пишет timestamp в heartbeat-файл."""
    from datetime import datetime, timezone

    interval = settings.heartbeat_interval_minutes * 60
    while True:
        try:
            ts = datetime.now(timezone.utc).isoformat()
            with open(settings.heartbeat_file, "w", encoding="utf-8") as f:
                f.write(ts)
            logger.debug("Heartbeat written: %s", ts)
        except Exception:
            logger.debug("Failed to write heartbeat", exc_info=True)
        await asyncio.sleep(interval)


async def run_chat_monitor(bot, session_factory_arg) -> None:
    """Запуск Chat Monitor как фоновой задачи внутри основного бота.

    Используется bot.py для запуска в одном процессе с ботом.
    При отмене (CancelledError) завершается gracefully.

    Args:
        bot: aiogram Bot instance (не используется, но передаётся для совместимости)
        session_factory_arg: SQLAlchemy session factory (заменяет глобальный)
    """
    _ensure_ready()
    ensure_session_path_ready()
    # SEC-FIX-1: session-файл — rootkey к аккаунту Telegram, только владельцу ОС
    restrict_file_permissions(settings.chat_monitor_session_path)
    try:
        from telethon import TelegramClient, events
    except ImportError as exc:
        raise RuntimeError("Telethon is not installed. Run `pip install -r requirements-lock.txt`.") from exc

    # Используем переданный session_factory вместо глобального
    global session_factory
    original_factory = session_factory
    session_factory = session_factory_arg

    try:
        client = TelegramClient(
            settings.chat_monitor_session_path,
            settings.chat_monitor_api_id,
            settings.chat_monitor_api_hash,
        )

        initial_config = await load_runtime_config()
        logger.info(
            "Chat monitor config: enabled=%s chats=%s min_score=%.2f",
            initial_config.is_enabled,
            len(initial_config.chats),
            initial_config.min_score,
        )

        candidate_queue: asyncio.Queue[tuple[object, ChatMonitorConfig]] = asyncio.Queue(
            maxsize=settings.chat_monitor_queue_size
        )

        async def candidate_worker(worker_id: int) -> None:
            while True:
                candidate, config = await candidate_queue.get()
                try:
                    lead = await process_candidate(
                        candidate,
                        session_factory,
                        owner_tg_id=config.owner_tg_id,
                        min_score=config.min_score,
                    )
                    if lead is not None:
                        logger.info(
                            "Chat lead saved: id=%s chat=%s worker=%s",
                            lead.id,
                            lead.source_chat,
                            worker_id,
                        )
                except AIError as exc:
                    logger.warning("LLM error in chat monitor worker: %s", exc)
                except Exception as exc:
                    logger.exception("Chat monitor worker failed")
                    capture_exception(exc)
                finally:
                    candidate_queue.task_done()

        worker_tasks = [
            asyncio.create_task(candidate_worker(index), name=f"chat-monitor-worker-{index}")
            for index in range(settings.chat_monitor_worker_count)
        ]

        @client.on(events.NewMessage())
        async def on_new_message(event) -> None:
            try:
                config = await load_runtime_config()
                if not config.is_enabled or not config.chats:
                    return
                if not await event_matches_chat_refs(event, config.chats):
                    return
                candidate = await candidate_from_event(event)
                if candidate is None:
                    return
                try:
                    candidate_queue.put_nowait((candidate, config))
                except asyncio.QueueFull:
                    logger.warning(
                        "Chat monitor queue full (%s), dropping message chat=%s user=%s",
                        settings.chat_monitor_queue_size,
                        candidate.source_chat,
                        candidate.user_id,
                    )
            except Exception as exc:
                logger.exception("Chat monitor message processing failed")
                # MONITORING-2: необработанный сбой обработки — в Sentry
                capture_exception(exc)

        logger.info(
            "Chat Monitor: authorizing phone ending %s (force_sms=%s)",
            _mask_phone(settings.chat_monitor_phone),
            settings.chat_monitor_force_sms,
        )

        # Render has no interactive terminal — we must have an existing .session file
        session_file = Path(settings.chat_monitor_session_path)
        if not session_file.exists():
            logger.warning(
                "Chat Monitor: session file %s not found. "
                "Run locally or use QR login to create .session, "
                "then encode to base64 and commit as chat_monitor.session.b64.",
                settings.chat_monitor_session_path,
            )
            raise RuntimeError(
                "Chat Monitor requires an existing .session file. "
                "Encode chat_monitor.session to base64 and commit "
                "as chat_monitor.session.b64"
            )

        try:
            await client.start(
                phone=normalize_phone(settings.chat_monitor_phone),
                force_sms=settings.chat_monitor_force_sms,
            )
        except EOFError:
            logger.error(
                "Chat Monitor: EOFError during auth — no interactive terminal "
                "on Render. Create .session locally and commit as .b64."
            )
            raise

        restrict_file_permissions(settings.chat_monitor_session_path)

        me = await client.get_me()
        logger.info(
            "Chat Monitor: authenticated as user_id=%s username=%s",
            getattr(me, "id", None),
            getattr(me, "username", None),
        )

        # Запускаем heartbeat параллельно
        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        try:
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            logger.info("Chat Monitor: graceful shutdown requested")
            raise
        finally:
            heartbeat_task.cancel()
            for task in worker_tasks:
                task.cancel()
            try:
                await asyncio.wait_for(heartbeat_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            await client.disconnect()
            logger.info("Chat Monitor: disconnected")
    finally:
        # Восстанавливаем глобальный session_factory
        session_factory = original_factory


async def run() -> None:
    """Standalone запуск Chat Monitor (старый способ через python -m chat_monitor.runner).

    Для обратной совместимости. Новый способ: запуск вместе с ботом через bot.py.
    """
    if settings.environment.lower() == "production":
        raise RuntimeError(
            "Standalone Chat Monitor is disabled in production; run it embedded in bot.py"
        )
    _ensure_ready()
    ensure_session_path_ready()
    # SEC-FIX-1: session-файл — rootkey к аккаунту Telegram, только владельцу ОС
    restrict_file_permissions(settings.chat_monitor_session_path)
    try:
        from telethon import TelegramClient, events
    except ImportError as exc:
        raise RuntimeError("Telethon is not installed. Run `pip install -r requirements-lock.txt`.") from exc

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()

    client = TelegramClient(
        settings.chat_monitor_session_path,
        settings.chat_monitor_api_id,
        settings.chat_monitor_api_hash,
    )

    initial_config = await load_runtime_config()
    logger.info(
        "Chat monitor config loaded: enabled=%s chats=%s min_score=%.2f",
        initial_config.is_enabled,
        len(initial_config.chats),
        initial_config.min_score,
    )

    @client.on(events.NewMessage())
    async def on_new_message(event) -> None:
        try:
            config = await load_runtime_config()
            if not config.is_enabled or not config.chats:
                return
            if not await event_matches_chat_refs(event, config.chats):
                return
            candidate = await candidate_from_event(event)
            if candidate is None:
                return
            lead = await process_candidate(
                candidate,
                session_factory,
                owner_tg_id=config.owner_tg_id,
                min_score=config.min_score,
            )
        except AIError as exc:
            logger.warning("LLM parsing error while processing chat monitor message: %s", exc)
            return
        except Exception:
            logger.exception("Failed to process chat monitor message")
            return
        if lead is not None:
            logger.info("Saved chat lead id=%s source_chat=%s", lead.id, lead.source_chat)

    logger.info(
        "Starting Telethon authorization for phone ending %s. The login code is usually sent "
        "to the official Telegram app first, not always by SMS. force_sms=%s",
        _mask_phone(settings.chat_monitor_phone),
        settings.chat_monitor_force_sms,
    )
    await client.start(
        phone=normalize_phone(settings.chat_monitor_phone),
        force_sms=settings.chat_monitor_force_sms,
    )
    me = await client.get_me()
    logger.info(
        "Chat monitor started as user_id=%s username=%s",
        getattr(me, "id", None),
        getattr(me, "username", None),
    )

    # P-4: Запускаем heartbeat-задачу параллельно с Telethon
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        await client.run_until_disconnected()
    finally:
        heartbeat_task.cancel()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
