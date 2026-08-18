"""Утилиты для FSM-хендлеров: корректный приём текстового ввода (F3).

Пользователь в состоянии «пришли текст» может отправить фото, стикер, голосовое
или файл. Без явной проверки бот отвечал «пустое значение не подойдёт» и молча
оставался в состоянии, не объясняя ни причину, ни способ выйти. Здесь —
единая точка такой проверки с понятным сообщением и подсказкой /cancel.
"""

import logging

from aiogram.enums import ContentType
from aiogram.types import Message

logger = logging.getLogger(__name__)

CANCEL_HINT = "\n\nИли пришли /cancel для отмены."


async def get_text_input(message: Message, empty_reject: str) -> str | None:
    """Возвращает непустой текст от пользователя или None после ответа-отказа.

    При нетекстовом вводе (фото/стикер/voice/document/...) отвечает пользователю,
    почему отказ, и подсказывает /cancel — состояние не «зависает» молча.
    """
    if message.content_type != ContentType.TEXT:
        await message.answer(
            f"Пришли именно текст (без фото, стикеров, голосовых и файлов).{CANCEL_HINT}"
        )
        return None
    text = (message.text or "").strip()
    if not text:
        await message.answer(f"{empty_reject}{CANCEL_HINT}")
        return None
    return text
