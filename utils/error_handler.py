"""Глобальная обработка ошибок для предотвращения краха хендлеров (ERROR-1).

Перехватывает необработанные исключения в хендлерах и отправляет пользователю
user-friendly сообщение вместо падения бота. FSM состояние сбрасывается (F2) —
после исключения ссылаться на сохранённый FSM-context небезопасно.
"""

import logging
from typing import Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from utils.emoji_config import E
from utils.sentry import capture_exception

logger = logging.getLogger(__name__)

ERROR_MESSAGE = (
    f"{E.CROSS} Произошла ошибка при обработке запроса. "
    "Попробуй ещё раз. Если ты был в середине действия — оно сброшено, начни заново."
)


class ErrorHandlerMiddleware(BaseMiddleware):
    """Middleware: перехватывает необработанные исключения в хендлерах.

    Предотвращает полное падение бота при ошибках в бизнес-логике.
    FSM состояние СБРАСЫВАЕТСЯ (F2): иначе пользователь остаётся в
    SearchFSM.browsing и прочих состояниях, указывающих на уже испорченные
    данные, без понятного способа выйти — нажатия старых кнопок действуют
    на «забытый» контекст. Безопаснее начать сценарий заново.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable],
        event: TelegramObject,
        data: dict,
    ):
        try:
            return await handler(event, data)
        except Exception as exc:
            # Логируем полный трейс для отладки
            logger.exception("Unhandled exception in handler: %s", exc)
            # MONITORING-2: дублируем в Sentry (no-op если не настроен)
            capture_exception(exc)

            # F2: сбрасываем FSM — состояние после исключения недоверенное
            # (данные могли быть записаны частично, транзакция откачена).
            state = data.get("state")
            if state is not None:
                try:
                    await state.clear()
                except Exception as state_error:
                    logger.warning("Failed to clear FSM state on error: %s", state_error)

            # Пытаемся отправить пользователю сообщение об ошибке
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer(ERROR_MESSAGE, show_alert=True)
                    # Дополнительно отправляем текстовое сообщение
                    if event.message:
                        await event.message.answer(ERROR_MESSAGE)
                elif isinstance(event, Message):
                    await event.answer(ERROR_MESSAGE)
            except Exception as send_error:
                logger.error("Failed to send error message to user: %s", send_error)

            # Не пробрасываем исключение дальше - предотвращаем крах бота
            return None
