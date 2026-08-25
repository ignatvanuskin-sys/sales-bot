"""
Конфигурация кастомных Telegram эмодзи из пака tgmacicons
https://t.me/addemoji/tgmacicons

E.*  — HTML-версии (<tg-emoji>) для текстов сообщений с parse_mode="HTML".
P.*  — обычный юникод для show_alert() и других мест без HTML-рендеринга.

Как получить ID:
1. Откройте Telegram Desktop
2. Добавьте пак: https://t.me/addemoji/tgmacicons
3. Отправьте эмодзи боту @userinfobot
4. Скопируйте ID из ответа бота

ПРИМЕЧАНИЕ ОБ ID: несколько разных юникод-эмодзи осознанно ссылаются на
один и тот же emoji_id (алиасы одного визуала для смежных смыслов):
  телефоны 🤙/📞/☎️/📱, дома 🏠/🏘, календари 📅/🗓, галочки ✅/✓,
  крестики ❌/✗, списки 📝/📋, часы ⏲/🕐, анализ 🍑/📊, номера 🔢/🆔,
  художники/стиль 👨‍🎨/🎨, энергия ⚡/🎯, мастера 👩‍🎨/💈, стрелки ⬇️/👇.
Сами ID в Telegram не проверялись автоматически — при невалидном ID
бот откатится на обычный юникод через utils.safe_send (см. README).
"""

# Маппинг Unicode эмодзи -> ID кастомного эмодзи
CUSTOM_EMOJIS = {
    # Основные - интерфейс
    "🏘": "5257963315258204021",  # Дома (главное меню) - PREMIUM EMOJI
    "🍑": "5258330865674494479",  # Анализ (стикер) - PREMIUM EMOJI
    "🤙": "5258337316715373336",  # Телефон жест - PREMIUM EMOJI
    "👤": "5260399854500191689",  # Пользователь, клиент - PREMIUM EMOJI
    "🗓": "5258105663359294787",  # Календарь альтернативный (дата) - PREMIUM EMOJI
    "📅": "5258105663359294787",  # Календарь (дата) - PREMIUM EMOJI
    # Краткий список результатов / тип организации. Использую ПРОВЕРЕННЫЙ ID
    # "Люди" из предоставленного списка (семантически ближе к сущности/записи).
    "🏢": "5870772616305839506",  # Сущность/организация (People) - PREMIUM EMOJI

    # Статусы
    "✅": "5260726538302660868",  # Успех, подтверждение - PREMIUM EMOJI
    "❌": "5260342697075416641",  # Ошибка, отмена - PREMIUM EMOJI
    "✓": "5260726538302660868",   # Галочка - PREMIUM EMOJI
    "✗": "5260342697075416641",   # Крестик - PREMIUM EMOJI

    # Информация и действия
    "📌": "5258461531464539536",  # Пин, закрепить - PREMIUM EMOJI
    "📍": "5258509201306557640",  # Локация, адрес - PREMIUM EMOJI
    "➕": "5258108352008823107",  # Добавить, плюс - PREMIUM EMOJI
    "🔄": "5258420634785947640",  # Обновить, повторить - PREMIUM EMOJI
    "👥": "5258513401784573443",  # Группа людей, клиенты - PREMIUM EMOJI
    "📸": "5258205968025525531",  # Камера, фото - PREMIUM EMOJI
    "⭐": "5258165702707125574",  # Звезда, рейтинг - PREMIUM EMOJI
    "🔎": "5429571366384842791",  # Поиск, лупа - PREMIUM EMOJI
    "👁": "5253959125838090076",  # Глаз, просмотр - PREMIUM EMOJI
    "🔒": "5258476306152038031",  # Замок, безопасность - PREMIUM EMOJI
    "📂": "5257969839313526622",  # Папка, файлы - PREMIUM EMOJI
    # Художник/кисточка/стиль/ножницы (стрижка) — один визуал на все смыслы
    "👨‍🎨": "5258450450448915742",  # PREMIUM EMOJI
    "🎨": "5258450450448915742",  # Палитра, стиль - PREMIUM EMOJI
    "🔢": "5226513232549664618",  # Цифры, номер - PREMIUM EMOJI
    "🆔": "5226513232549664618",  # ID записи, номер - PREMIUM EMOJI

    # Время и расписание
    "🕘": "5199457120428249992",  # Часы 9:00 - PREMIUM EMOJI
    "⏲": "5258258882022612173",  # Таймер - PREMIUM EMOJI
    "🕐": "5258258882022612173",  # Часы (время)

    # Деньги и работа
    "💰": "5258204546391351475",  # Деньги, цена - PREMIUM EMOJI
    "📝": "5257965174979042426",  # Список, записи - PREMIUM EMOJI
    "📋": "5257965174979042426",  # Список, записи
    "❗": "5258474669769497337",  # Предупреждение/важно - PREMIUM EMOJI
    "💡": "5258216851472654189",  # Идея, совет - PREMIUM EMOJI
    "📖": "5258328383183396223",  # Книга, справка - PREMIUM EMOJI
    "💬": "5260535596941582167",  # Комментарий, отзыв - PREMIUM EMOJI

    # Дополнительные действия
    "✈️": "5258073068852485953",  # Отправить/отправлено - PREMIUM EMOJI
    "✍️": "5258331647358540449",  # Написать/редактировать - PREMIUM EMOJI
    "👏": "5258501105293205250",  # Аплодисменты/молодец - PREMIUM EMOJI
    "⚡": "5258152182150077732",  # Быстро/энергия - PREMIUM EMOJI
    "🎯": "5258152182150077732",  # Цель, специализация
    "🤚": "5260249440450520061",  # Стоп/рука - PREMIUM EMOJI
    "👩‍🎨": "5258215635996908355",  # Художник женщина/мастер - PREMIUM EMOJI
    "💈": "5258215635996908355",  # Барбер полюс (барбершоп)

    # Информация
    "ℹ️": "5258503720928288433",  # Информация - PREMIUM EMOJI
    "⬇️": "5258336354642697821",  # Стрелка вниз/указатель - PREMIUM EMOJI
    "👇": "5258336354642697821",  # Палец вниз, указатель

    # Дополнительные
    "📞": "5258337316715373336",  # Телефон - PREMIUM EMOJI
    "☎️": "5258337316715373336",  # Телефон альт - PREMIUM EMOJI
    "📱": "5258337316715373336",  # Мобильный телефон - PREMIUM EMOJI
    "🏠": "5257963315258204021",  # Дом (главное меню) - PREMIUM EMOJI
    "👨‍💼": "5260399854500191689",  # Мастер/Барбер - PREMIUM EMOJI
    "📊": "5258330865674494479",  # График, статистика, анализ - PREMIUM EMOJI
}


def emoji(unicode_emoji: str, fallback: bool = True) -> str:
    """Конвертирует Unicode эмодзи в кастомный Telegram эмодзи.

    Возвращает HTML-код <tg-emoji emoji-id="...">X</tg-emoji>,
    либо обычный эмодзи, если ID не найден и fallback=True.
    """
    emoji_id = CUSTOM_EMOJIS.get(unicode_emoji)

    if emoji_id and emoji_id != "ЗАМЕНИТЕ_НА_REAL_ID":
        return f'<tg-emoji emoji-id="{emoji_id}">{unicode_emoji}</tg-emoji>'
    return unicode_emoji if fallback else ""


class E:
    """HTML-версии эмодзи для текстов сообщений (parse_mode='HTML')."""

    # Основные
    COMPANY = emoji("🏢")  # PREMIUM - тип организации/сущность
    SCISSORS = emoji("👨🎨")
    BARBER = emoji("💈")
    CALENDAR = emoji("📅")  # PREMIUM
    CALENDAR_ALT = emoji("🗓")  # PREMIUM
    CLOCK = emoji("🕐")
    CLOCK_9 = emoji("🕘")  # PREMIUM - часы 9:00
    TIMER = emoji("⏲")  # PREMIUM - таймер
    MASTER = emoji("👨‍💼")  # PREMIUM
    USER = emoji("👤")  # PREMIUM
    HOME = emoji("🏠")  # PREMIUM - главное меню
    HOUSES = emoji("🏘")  # PREMIUM - главное меню альт

    # Статусы
    CHECK = emoji("✅")  # PREMIUM
    CROSS = emoji("❌")  # PREMIUM
    EXCLAMATION = emoji("❗")  # PREMIUM - важно
    WARNING = emoji("⚠️")  # Предупреждение (нет в паке — обычный юникод)
    CHECK_SMALL = emoji("✓")  # PREMIUM
    CROSS_SMALL = emoji("✗")  # PREMIUM

    # Информация
    LIST = emoji("📋")
    NOTE = emoji("📝")  # PREMIUM - заметка
    CHART = emoji("📊")  # PREMIUM - анализ
    PEACH = emoji("🍑")  # PREMIUM - анализ стикер
    MONEY = emoji("💰")
    LOCATION = emoji("📍")  # PREMIUM
    PIN = emoji("📌")  # PREMIUM
    PHONE = emoji("📞")  # PREMIUM
    PHONE_GESTURE = emoji("🤙")  # PREMIUM - телефон жест
    MOBILE = emoji("📱")  # PREMIUM

    # Действия
    TARGET = emoji("🎯")
    CLAP = emoji("👏")  # PREMIUM - аплодисменты
    STAR = emoji("⭐")  # PREMIUM - рейтинг
    IDEA = emoji("💡")
    BOOK = emoji("📖")
    ID = emoji("🆔")  # PREMIUM - ID номер
    NUMBER = emoji("🔢")  # PREMIUM - цифры
    COMMENT = emoji("💬")
    PLUS = emoji("➕")  # PREMIUM - добавить
    RELOAD = emoji("🔄")  # PREMIUM - обновить
    PEOPLE = emoji("👥")  # PREMIUM - группа
    CAMERA = emoji("📸")  # PREMIUM - фото
    SEARCH = emoji("🔎")  # PREMIUM - поиск
    EYE = emoji("👁")  # PREMIUM - просмотр
    LOCK = emoji("🔒")  # PREMIUM - безопасность
    FOLDER = emoji("📂")  # PREMIUM - папка
    ARTIST = emoji("👨‍🎨")  # PREMIUM - художник/стиль (мужчина)
    ARTIST_WOMAN = emoji("👩‍🎨")  # PREMIUM - художник/мастер (женщина)
    PALETTE = emoji("🎨")  # PREMIUM - палитра
    PLANE = emoji("✈️")  # PREMIUM - отправить
    WRITING = emoji("✍️")  # PREMIUM - написать/редактировать
    LIGHTNING = emoji("⚡")  # PREMIUM - быстро/энергия
    HAND_STOP = emoji("🤚")  # PREMIUM - стоп/рука
    INFO = emoji("ℹ️")  # PREMIUM - информация
    POINT_DOWN = emoji("👇")  # Палец вниз, указатель
    ARROW_DOWN = emoji("⬇️")  # PREMIUM - стрелка вниз
    LINK = emoji("🔗")  # Ссылка (нет в паке — обычный юникод)


class P:
    """Обычные юникод-эмодзи для show_alert и других мест без HTML."""

    CHECK = "✅"
    CROSS = "❌"
    WARNING = "⚠️"
    EXCLAMATION = "❗"
    EMPTY = "📭"
    INFO = "ℹ️"
    LOCK = "🔒"
    SCISSORS = "✂️"
    CALENDAR = "📅"
    CLOCK = "🕐"
    TIMER = "⏲"
    STAR = "⭐"
    PARTY = "🎉"
    CLAP = "👏"
    MONEY = "💰"
    RELOAD = "🔄"
    USER = "👤"
    PHONE = "📞"
    HOUSES = "🏘"
    NOTE = "📝"
    IDEA = "💡"
    BOOK = "📖"
    COMMENT = "💬"
    PLANE = "✈️"
    WRITING = "✍️"
    LIGHTNING = "⚡"
    HAND_STOP = "🤚"
    ARROW_DOWN = "⬇️"
    POINT_DOWN = "👇"
    PLUS = "➕"
    ARTIST = "👨‍🎨"
    ARTIST_WOMAN = "👩‍🎨"
    PEACH = "🍑"
    LOCATION = "📍"
    PIN = "📌"
    SEARCH = "🔎"
    EYE = "👁"
    FOLDER = "📂"
    PALETTE = "🎨"
    BARBER = "💈"
    CHART = "📊"
    PEOPLE = "👥"
    CAMERA = "📸"


def check_emoji_config() -> dict:
    """Проверяет конфигурацию: total, configured, missing, percent."""
    total = len(CUSTOM_EMOJIS)
    missing = sum(1 for v in CUSTOM_EMOJIS.values() if v == "ЗАМЕНИТЕ_НА_REAL_ID")
    configured = total - missing

    return {
        "total": total,
        "configured": configured,
        "missing": missing,
        "percent": round(configured / total * 100, 1) if total > 0 else 0,
    }
