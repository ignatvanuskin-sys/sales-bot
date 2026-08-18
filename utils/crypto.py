"""SEC-FIX-5: Прозрачное шифрование ПДн-колонок в БД (SQLAlchemy TypeDecorator).

Используется Fernet (AES-128-CBC + HMAC-SHA256, из пакета cryptography — уже
транзитивная зависимость через Telethon). Шифрование ВКЛЮЧАЕТСЯ заданием
PII_ENCRYPTION_KEY в .env; без ключа колонки работают как обычный Text
(обратная совместимость с существующими незашифрованными БД).

Зашифрованное значение хранится с префиксом ENC_PREFIX — это позволяет:
  * отличать шифротекст от plaintext (смешанная БД в переходный период);
  * корректно читать старые незашифрованные строки после включения ключа;
  * не падать, а отдавать plaintext, если ключ убрали.

Ограничения: по зашифрованной колонке нельзя делать WHERE/LIKE/сортировку на
уровне SQL (детерминистическое шифрование не используем осознанно — оно слабее).
Поэтому шифруем только message_text (поиск по нему не нужен); контакты для
связи (phone/username) остаются открытыми — это осознанный компромисс UX,
задокументированный в README.
"""

import logging

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from config import settings

logger = logging.getLogger(__name__)

# Префикс шифротекста в БД
ENC_PREFIX = "enc::v1::"

_fernet = None
_fernet_failed = False


def _get_fernet():
    """Ленивая инициализация Fernet по ключу из конфига. None = шифрование выкл."""
    global _fernet, _fernet_failed
    if _fernet is not None:
        return _fernet
    if _fernet_failed:
        return None
    if not settings.pii_encryption_key:
        return None
    try:
        from cryptography.fernet import Fernet

        _fernet = Fernet(settings.pii_encryption_key.encode())
        logger.info("PII encryption: enabled (Fernet)")
        return _fernet
    except Exception as exc:
        # SEC-H1: ключ ЗАДАН, но невалиден. Раньше это молча отключало шифрование
        # и новые ПДн уходили в БД открытым текстом при вере оператора в защиту.
        # Стартовый config_validator такой конфиг блокирует, но ключ мог стать
        # невалидным ПОСЛЕ старта (ротация/перезапись .env без рестарта —
        # _fernet закэширован, это защита на случай обращений до рестарта).
        _fernet_failed = True
        logger.error(
            "PII encryption: invalid PII_ENCRYPTION_KEY (%s) — refusing to write plaintext", exc
        )
        return None


def encrypt_value(value: str | None) -> str | None:
    """Шифрует строку (с префиксом). None/пусто и отсутствие ключа — как есть.

    SEC-H1 (fail-closed): если ключ ЗАДАН, но невалиден — НЕ пишем plaintext,
    а падаем. Иначе оператор с опечаткой в ключе будет считать ПДн защищёнными,
    а они окажутся в БД открытым текстом. config_validator ловит это при старте;
    здесь — страховка на случай ротации ключа без рестарта процесса.
    """
    if value is None or value == "":
        return value
    f = _get_fernet()
    if f is None:
        if settings.pii_encryption_key:
            raise RuntimeError(
                "PII_ENCRYPTION_KEY задан, но невалиден — шифрование недоступно. "
                "Исправь ключ или убери его из .env. Plaintext-запись отклонена (SEC-H1)."
            )
        return value
    # Не шифруем повторно уже зашифрованное
    if value.startswith(ENC_PREFIX):
        return value
    token = f.encrypt(value.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt_value(value: str | None) -> str | None:
    """Расшифровывает строку с префиксом. Без префикса/ключа — возвращает как есть."""
    if value is None or value == "":
        return value
    if not value.startswith(ENC_PREFIX):
        return value  # plaintext (старые данные или шифрование было выкл.)
    f = _get_fernet()
    if f is None:
        # Ключ убрали, а данные зашифрованы — честно предупреждаем, не падаем
        logger.warning("PII encryption: encrypted value present but key is missing")
        return "[зашифровано: ключ недоступен]"
    token = value[len(ENC_PREFIX):]
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as exc:
        logger.error("PII decryption failed: %s", exc)
        return "[ошибка расшифровки]"


class EncryptedText(TypeDecorator):
    """SQLAlchemy-тип: Text снаружи, Fernet-шифротекст в БД.

    Прозрачен для кода: ORM читает/пишет обычные строки, шифрование на границе БД.
    Кэширует длину для VARCHAR-колонок не требует — используется на Text.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)


def reset_fernet() -> None:
    """Сбрасывает кэшированный Fernet (для тестов)."""
    global _fernet, _fernet_failed
    _fernet = None
    _fernet_failed = False
