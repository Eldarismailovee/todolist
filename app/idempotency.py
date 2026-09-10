"""Серверный Idempotency-Key для безопасного повтора мутаций.

Запись привязана к владельцу и хешу тела запроса: тот же ключ с другим телом —
конфликт, а не молчаливое повторение чужого результата. Клиент повторяет
мутацию только с этим ключом; повтор без него запрещён.
"""

import hashlib
import json
from typing import Any

from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings

PENDING = "pending"
DONE = "done"


def body_fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _key(settings: Settings, user_id: int, raw_key: str) -> str:
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return f"{settings.key_prefix}idem:{user_id}:{digest}"


def validate_key(raw_key: str | None) -> str | None:
    if raw_key is None:
        return None
    if not (8 <= len(raw_key) <= 200) or not raw_key.isascii() or not raw_key.isprintable():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Некорректный заголовок Idempotency-Key")
    return raw_key


async def begin(
    redis: Redis, settings: Settings, user_id: int, raw_key: str, fingerprint: str
) -> dict | None:
    """Резервирует ключ. Возвращает готовый ответ, если запрос уже выполнялся."""
    key = _key(settings, user_id, raw_key)
    record = json.dumps({"status": PENDING, "fingerprint": fingerprint})
    try:
        reserved = await redis.set(key, record, ex=settings.idempotency_ttl_seconds, nx=True)
        if reserved:
            return None
        stored = await redis.get(key)
    except RedisError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Хранилище идемпотентности недоступно"
        ) from exc

    if stored is None:
        # Запись истекла между SET NX и GET: считаем запрос новым.
        return None
    previous = json.loads(stored)
    if previous.get("fingerprint") != fingerprint:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Idempotency-Key уже использован с другим телом запроса"
        )
    if previous.get("status") != DONE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Запрос с этим Idempotency-Key ещё выполняется"
        )
    return previous.get("response")


async def complete(
    redis: Redis,
    settings: Settings,
    user_id: int,
    raw_key: str,
    fingerprint: str,
    response: dict,
) -> None:
    record = json.dumps(
        {"status": DONE, "fingerprint": fingerprint, "response": response}, default=str
    )
    try:
        await redis.set(
            _key(settings, user_id, raw_key), record, ex=settings.idempotency_ttl_seconds
        )
    except RedisError:
        # Мутация уже выполнена: неудачная отметка не должна её отменять.
        pass


async def release(redis: Redis, settings: Settings, user_id: int, raw_key: str) -> None:
    """Снимает резерв, если мутация не состоялась, чтобы повтор был возможен."""
    try:
        await redis.delete(_key(settings, user_id, raw_key))
    except RedisError:
        pass
