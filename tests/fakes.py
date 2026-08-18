"""Самодельные Message/CallbackQuery/FSMContext для прямого вызова хендлеров.

Без живого Telegram и без мокания всего aiogram: только те поля и методы,
которые реально используют хендлеры.
"""


class FakeUser:
    def __init__(self, user_id: int, username: str | None = "tester"):
        self.id = user_id
        self.username = username


class FakeMessage:
    """Запоминает всё, что бот 'отправил' или 'отредактировал'."""

    def __init__(self, text: str | None = None, user_id: int = 100, log: list | None = None, content_type: str = "text"):
        self.text = text
        self.content_type = content_type
        self.from_user = FakeUser(user_id)
        # Общий лог всех действий: ("answer"|"edit_text"|"edit_markup"|"delete", text, markup)
        self.log = log if log is not None else []

    async def answer(self, text: str, reply_markup=None) -> "FakeMessage":
        self.log.append(("answer", text, reply_markup))
        return FakeMessage(text=text, user_id=self.from_user.id, log=self.log, content_type=self.content_type)

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.log.append(("edit_text", text, reply_markup))

    async def edit_reply_markup(self, reply_markup=None) -> None:
        self.log.append(("edit_markup", None, reply_markup))

    async def delete(self) -> None:
        self.log.append(("delete", None, None))

    # --- помощники для assert'ов ---
    def texts(self, kind: str | None = None) -> list[str]:
        return [t for (k, t, _) in self.log if t is not None and (kind is None or k == kind)]

    def last_text(self) -> str:
        texts = self.texts()
        return texts[-1] if texts else ""


class FakeCallback:
    def __init__(self, data: str, user_id: int = 100, message: FakeMessage | None = None):
        self.data = data
        self.from_user = FakeUser(user_id)
        self.message = message or FakeMessage(user_id=user_id)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))

    def alert_texts(self) -> list[str]:
        return [t for (t, alert) in self.answers if alert and t]


class FakeState:
    """Минимальный FSMContext: get_data/update_data/set_state/clear."""

    def __init__(self, data: dict | None = None, state=None):
        self.data = dict(data or {})
        self.state = state

    async def get_data(self) -> dict:
        return dict(self.data)

    async def get_state(self):
        return self.state

    async def update_data(self, **kwargs) -> dict:
        self.data.update(kwargs)
        return dict(self.data)

    async def set_state(self, state=None) -> None:
        self.state = state

    async def clear(self) -> None:
        self.data = {}
        self.state = None
