"""Настройки уведомлений о дедлайнах."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..dependencies import CurrentPrincipal, Db, TelegramDep
from ..models import NotificationPrefs
from ..schemas import NotificationPrefsSchema

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _prefs(db: Db, user_id: int) -> NotificationPrefs:
    prefs = await db.scalar(select(NotificationPrefs).where(NotificationPrefs.user_id == user_id))
    if prefs is None:
        prefs = NotificationPrefs(user_id=user_id)
        db.add(prefs)
        await db.flush()
    return prefs


@router.get("/settings", response_model=NotificationPrefsSchema)
async def read_settings(principal: CurrentPrincipal, db: Db):
    prefs = await _prefs(db, principal.user_id)
    result = NotificationPrefsSchema.model_validate(prefs)
    await db.commit()
    return result


@router.put("/settings", response_model=NotificationPrefsSchema)
async def update_settings(payload: NotificationPrefsSchema, principal: CurrentPrincipal, db: Db):
    if payload.telegram_enabled and not payload.telegram_chat_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Для Telegram нужен chat_id: получите его у бота командой /start",
        )
    prefs = await _prefs(db, principal.user_id)
    prefs.email_enabled = payload.email_enabled
    prefs.telegram_enabled = payload.telegram_enabled
    prefs.telegram_chat_id = payload.telegram_chat_id
    prefs.lead_time_minutes = payload.lead_time_minutes
    await db.flush()
    result = NotificationPrefsSchema.model_validate(prefs)
    await db.commit()
    return result


@router.post("/test", status_code=status.HTTP_204_NO_CONTENT)
async def send_test(principal: CurrentPrincipal, db: Db, telegram: TelegramDep):
    """Проверка связи: без неё неверный chat_id обнаружился бы только в дедлайн."""
    prefs = await _prefs(db, principal.user_id)
    await db.commit()
    if not prefs.telegram_enabled or not prefs.telegram_chat_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Telegram не настроен")
    if not await telegram.send(prefs.telegram_chat_id, "Todo App: уведомления подключены."):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram не принял сообщение")
