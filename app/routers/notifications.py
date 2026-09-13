"""Настройки уведомлений о дедлайнах и подключение Telegram-чата."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from .. import telegram_link
from ..dependencies import CurrentPrincipal, Db, RedisDep, SettingsDep, TelegramDep
from ..models import NotificationPrefs
from ..schemas import (
    NotificationPrefsSchema,
    NotificationPrefsUpdate,
    TelegramConfirmRequest,
    TelegramLinkChallenge,
    TelegramLinkRequest,
)
from ..security import enforce_rate_limit

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _prefs(db: Db, user_id: int) -> NotificationPrefs:
    prefs = await db.scalar(select(NotificationPrefs).where(NotificationPrefs.user_id == user_id))
    if prefs is None:
        prefs = NotificationPrefs(user_id=user_id)
        db.add(prefs)
        await db.flush()
    return prefs


async def _limit_telegram_actions(redis, settings, user_id: int, action: str) -> None:
    """Свой счётчик на пользователя: каждое действие шлёт сообщение в чат."""
    await enforce_rate_limit(
        redis,
        settings,
        f"telegram:{action}:{user_id}",
        settings.otp_request_limit,
        settings.otp_request_window_seconds,
    )


@router.get("/settings", response_model=NotificationPrefsSchema)
async def read_settings(principal: CurrentPrincipal, db: Db):
    prefs = await _prefs(db, principal.user_id)
    result = NotificationPrefsSchema.model_validate(prefs)
    await db.commit()
    return result


@router.put("/settings", response_model=NotificationPrefsSchema)
async def update_settings(payload: NotificationPrefsUpdate, principal: CurrentPrincipal, db: Db):
    prefs = await _prefs(db, principal.user_id)
    if payload.telegram_enabled and not prefs.telegram_chat_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Сначала подключите чат: сервер пришлёт в него код подтверждения",
        )
    prefs.email_enabled = payload.email_enabled
    prefs.telegram_enabled = payload.telegram_enabled
    prefs.lead_time_minutes = payload.lead_time_minutes
    await db.flush()
    result = NotificationPrefsSchema.model_validate(prefs)
    await db.commit()
    return result


@router.post(
    "/telegram/link",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TelegramLinkChallenge,
)
async def link_telegram(
    payload: TelegramLinkRequest,
    principal: CurrentPrincipal,
    redis: RedisDep,
    settings: SettingsDep,
    telegram: TelegramDep,
):
    """Отправить код подтверждения в указанный чат.

    Введённое число само по себе ничего не доказывает: с ним можно было бы
    подписать чужой чат на свои уведомления. Прочитать код может только тот,
    у кого есть доступ к этому чату.
    """
    await _limit_telegram_actions(redis, settings, principal.user_id, "link")
    code = await telegram_link.issue(redis, settings, principal.user_id, payload.chat_id)
    message = (
        f"Код подключения Todo App: {code}\n"
        "Введите его в настройках уведомлений. Если вы этого не запрашивали — "
        "просто не вводите код."
    )
    if not await telegram.send(payload.chat_id, message):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram не принял сообщение")
    return {"code_sent": True, "expires_in": settings.otp_ttl_seconds}


@router.post("/telegram/confirm", response_model=NotificationPrefsSchema)
async def confirm_telegram(
    payload: TelegramConfirmRequest,
    principal: CurrentPrincipal,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Подтвердить чат кодом и включить доставку в него."""
    await _limit_telegram_actions(redis, settings, principal.user_id, "confirm")
    chat_id = await telegram_link.confirm(redis, settings, principal.user_id, payload.code)
    if chat_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный или истёкший код")

    prefs = await _prefs(db, principal.user_id)
    prefs.telegram_chat_id = chat_id
    prefs.telegram_enabled = True
    await db.flush()
    result = NotificationPrefsSchema.model_validate(prefs)
    await db.commit()
    return result


@router.delete("/telegram", response_model=NotificationPrefsSchema)
async def unlink_telegram(principal: CurrentPrincipal, db: Db):
    """Отключить чат. Повторное подключение снова требует подтверждения."""
    prefs = await _prefs(db, principal.user_id)
    prefs.telegram_chat_id = None
    prefs.telegram_enabled = False
    await db.flush()
    result = NotificationPrefsSchema.model_validate(prefs)
    await db.commit()
    return result


@router.post("/test", status_code=status.HTTP_204_NO_CONTENT)
async def send_test(
    principal: CurrentPrincipal,
    db: Db,
    redis: RedisDep,
    settings: SettingsDep,
    telegram: TelegramDep,
):
    """Проверка связи: без неё неверный chat_id обнаружился бы только в дедлайн."""
    # Маршрут отправляет сообщение в чат, поэтому ограничен так же, как выдача
    # кода: иначе им можно было бы завалить подключённый чат.
    await _limit_telegram_actions(redis, settings, principal.user_id, "test")
    prefs = await _prefs(db, principal.user_id)
    await db.commit()
    if not prefs.telegram_enabled or not prefs.telegram_chat_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Telegram не настроен")
    if not await telegram.send(prefs.telegram_chat_id, "Todo App: уведомления подключены."):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram не принял сообщение")
