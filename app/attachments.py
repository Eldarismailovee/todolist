"""Подписанные ссылки на файлы.

Тег `<img>` не может отправить заголовок Authorization, а access token
одноразовый — по нему картинку не покажешь. Поэтому в документе хранится
«голый» путь `/api/v1/files/{id}`, а при чтении сервер подставляет ссылку с
подписью и коротким сроком жизни. Ссылка — доступ на время, а не навсегда.
"""

import base64
import hmac
import re
import time
from hashlib import sha256
from typing import Any

from .config import Settings

FILE_PATH = re.compile(r"^/api/v1/files/([0-9a-fA-F-]{36})(?:\?.*)?$")
SIGNED_URL_TTL = 3600


def _signature(settings: Settings, attachment_id: str, expires_at: int) -> str:
    message = f"{attachment_id}:{expires_at}".encode()
    digest = hmac.new(settings.secret_key.encode(), message, sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def sign_url(settings: Settings, attachment_id: str, ttl: int = SIGNED_URL_TTL) -> str:
    expires_at = int(time.time()) + ttl
    signature = _signature(settings, attachment_id, expires_at)
    return f"/api/v1/files/{attachment_id}?exp={expires_at}&sig={signature}"


def verify_signature(settings: Settings, attachment_id: str, expires: str, signature: str) -> bool:
    if not expires.isdigit():
        return False
    expires_at = int(expires)
    if expires_at <= time.time():
        return False
    expected = _signature(settings, attachment_id, expires_at)
    return hmac.compare_digest(expected, signature)


def _map_urls(node: Any, transform) -> Any:
    """Обходит документ и заменяет src у изображений."""
    if isinstance(node, list):
        return [_map_urls(item, transform) for item in node]
    if not isinstance(node, dict):
        return node

    result = dict(node)
    attrs = result.get("attrs")
    if isinstance(attrs, dict) and isinstance(attrs.get("src"), str):
        attrs = dict(attrs)
        attrs["src"] = transform(attrs["src"])
        result["attrs"] = attrs
    if "content" in result:
        result["content"] = _map_urls(result["content"], transform)
    return result


def strip_signatures(content: Any) -> Any:
    """Перед сохранением: подпись из документа убирается, остаётся путь."""

    def transform(src: str) -> str:
        match = FILE_PATH.match(src)
        return f"/api/v1/files/{match.group(1)}" if match else src

    return _map_urls(content, transform) if content is not None else None


def sign_content(settings: Settings, content: Any) -> Any:
    """Перед отдачей клиенту: путь превращается в подписанную ссылку."""

    def transform(src: str) -> str:
        match = FILE_PATH.match(src)
        return sign_url(settings, match.group(1)) if match else src

    return _map_urls(content, transform) if content is not None else None
