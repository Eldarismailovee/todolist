"""Ограничение фактически прочитанного тела запроса.

Проверки одного `Content-Length` недостаточно: заголовок может отсутствовать
(chunked) или не совпадать с реальным потоком. Счётчик считает байты по мере
чтения и обрывает запрос при превышении лимита.

Лимит один на запрос, но не один на приложение: загрузка файла и JSON-мутация
имеют разные обещанные размеры. Общий лимит по меньшему из них молча ломал бы
загрузку, общий по большему — снимал бы защиту с остальных маршрутов.
"""

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOO_LARGE_BODY = b'{"detail":"Request body too large"}'


class MaxBodySizeMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        upload_max_bytes: int,
        upload_path: str,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes
        # Ровно один путь и метод: послабление не должно распространяться на
        # остальные операции с файлами (удаление, выдачу) и на подпути.
        self.upload_path = upload_path

    def _limit_for(self, scope: Scope) -> int:
        if scope.get("method") == "POST" and scope.get("path") == self.upload_path:
            return self.upload_max_bytes
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope)

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(send)
            return

        received = 0
        too_large = False

        async def counting_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    # Обрываем поток: обработчик получит усечённое тело и не
                    # успеет ничего записать в БД.
                    return {"type": "http.disconnect"}
            return message

        started = False
        rejected = False

        async def guarded_send(message: Message) -> None:
            """Ответ обработчика на усечённом теле подменяется на 413.

            После подмены нельзя пропускать ничего: обработчик продолжает слать
            свои http.response.body, и они дописались бы в уже завершённый ответ.
            """
            nonlocal started, rejected
            if rejected:
                return
            if too_large and not started:
                rejected = True
                await self._reject(send)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except ClientDisconnect:
            # Обрыв устроили мы сами; настоящий разрыв соединения обработчик
            # тоже видит так, и в обоих случаях писать в сокет уже нечего.
            pass

        # Обработчик мог не ответить вовсе — например, упасть на чтении тела.
        if too_large and not started and not rejected:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(TOO_LARGE_BODY)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": TOO_LARGE_BODY})
