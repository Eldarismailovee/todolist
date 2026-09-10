"""Ограничение фактически прочитанного тела запроса.

Проверки одного `Content-Length` недостаточно: заголовок может отсутствовать
(chunked) или не совпадать с реальным потоком. Счётчик считает байты по мере
чтения и обрывает запрос при превышении лимита.
"""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOO_LARGE_BODY = b'{"detail":"Request body too large"}'


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0
        too_large = False

        async def counting_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    # Обрываем поток: обработчик получит усечённое тело и не
                    # успеет ничего записать в БД.
                    return {"type": "http.disconnect"}
            return message

        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if too_large and not started:
                if message["type"] == "http.response.start":
                    started = True
                    await self._reject(send)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

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
