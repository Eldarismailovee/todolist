"""Вход через Google и GitHub (authorization code flow).

Секрет клиента и обмен кода живут на сервере: браузер получает только адрес
провайдера. `state` хранится в Redis и гасится при возврате — повторное
использование ссылки-возврата не создаёт вторую сессию. Для Google
дополнительно используется PKCE.
"""

import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis

from .config import Settings

logger = logging.getLogger(__name__)

# PKCE поддерживают оба, но у GitHub он необязателен; включаем там, где
# поведение проверено документацией провайдера.
PKCE_PROVIDERS = frozenset({"google"})


class OAuthError(Exception):
    """Обмен не состоялся: провайдер отказал или ответ не разобран."""


@dataclass(slots=True)
class OAuthIdentity:
    provider: str
    account_id: str
    email: str | None
    # Связывать внешний аккаунт с существующим по e-mail можно только когда
    # провайдер подтвердил владение адресом, иначе чужой аккаунт угоняется
    # регистрацией с тем же адресом у провайдера.
    email_verified: bool
    display_name: str | None
    avatar_url: str | None


def redirect_uri(settings: Settings, provider: str) -> str:
    return f"{settings.oauth_redirect_base}/api/v1/auth/oauth/{provider}/callback"


def _state_key(settings: Settings, state: str) -> str:
    return f"{settings.key_prefix}oauth:state:{state}"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def start(redis: Redis, settings: Settings, provider: str) -> str:
    """Возвращает адрес провайдера и запоминает state на время перехода."""
    config = settings.oauth_provider(provider)
    if not config["client_id"]:
        raise OAuthError(f"Провайдер {provider} не настроен")

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48) if provider in PKCE_PROVIDERS else None
    await redis.set(
        _state_key(settings, state),
        json.dumps({"provider": provider, "verifier": verifier}),
        ex=settings.oauth_state_ttl_seconds,
    )

    params = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri(settings, provider),
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
    }
    if verifier:
        params["code_challenge"] = _challenge(verifier)
        params["code_challenge_method"] = "S256"
    return f"{config['authorize_url']}?{urlencode(params)}"


async def consume_state(redis: Redis, settings: Settings, provider: str, state: str) -> str | None:
    """Гасит state атомарно и возвращает PKCE-verifier, если он был."""
    if not state:
        raise OAuthError("Отсутствует state")
    raw = await redis.getdel(_state_key(settings, state))
    if raw is None:
        raise OAuthError("Неизвестный или использованный state")
    stored = json.loads(raw)
    if stored.get("provider") != provider:
        raise OAuthError("state принадлежит другому провайдеру")
    return stored.get("verifier")


async def exchange(
    settings: Settings, provider: str, code: str, verifier: str | None
) -> OAuthIdentity:
    config = settings.oauth_provider(provider)
    data = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": code,
        "redirect_uri": redirect_uri(settings, provider),
        "grant_type": "authorization_code",
    }
    if verifier:
        data["code_verifier"] = verifier

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            token_response = await client.post(
                config["token_url"], data=data, headers={"Accept": "application/json"}
            )
        except httpx.HTTPError as error:
            raise OAuthError(f"Провайдер недоступен: {error}") from error

        if token_response.status_code >= 400:
            raise OAuthError("Провайдер отклонил обмен кода")
        payload = token_response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise OAuthError("В ответе провайдера нет access_token")

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        try:
            profile_response = await client.get(config["userinfo_url"], headers=headers)
        except httpx.HTTPError as error:
            raise OAuthError(f"Профиль недоступен: {error}") from error
        if profile_response.status_code >= 400:
            raise OAuthError("Провайдер не отдал профиль")
        profile = profile_response.json()

        if provider == "google":
            return OAuthIdentity(
                provider=provider,
                account_id=str(profile.get("sub") or ""),
                email=profile.get("email"),
                email_verified=bool(profile.get("email_verified")),
                display_name=profile.get("name"),
                avatar_url=profile.get("picture"),
            )

        # Адрес из профиля GitHub может быть не подтверждён, поэтому список
        # адресов запрашивается всегда: только он говорит о подтверждении.
        email = profile.get("email")
        email_verified = False
        try:
            emails_response = await client.get(settings.github_emails_url, headers=headers)
            if emails_response.status_code < 400:
                entries = emails_response.json()
                primary = next(
                    (entry for entry in entries if entry.get("primary") and entry.get("verified")),
                    None,
                )
                if primary:
                    email = primary.get("email")
                    email_verified = True
        except httpx.HTTPError:
            logger.info("Не удалось получить список адресов GitHub")

        return OAuthIdentity(
            provider=provider,
            account_id=str(profile.get("id") or ""),
            email=email,
            email_verified=email_verified,
            display_name=profile.get("name") or profile.get("login"),
            avatar_url=profile.get("avatar_url"),
        )
