"""Служебные маршруты для сквозных тестов.

Роутер подключается ТОЛЬКО когда `ENABLE_TESTING_ENDPOINTS=true`: в обычной
конфигурации этих путей не существует вовсе, а не «существуют и запрещены».
Код подтверждения хранится хешем, поэтому e2e иначе не смог бы его узнать.
"""

from fastapi import APIRouter, HTTPException, Query, status

from ..dependencies import RedisDep, SettingsDep

router = APIRouter(prefix="/testing", tags=["testing"])


def testing_otp_key(prefix: str, email: str, purpose: str) -> str:
    return f"{prefix}testing:otp:{email.lower()}:{purpose}"


@router.get("/otp")
async def read_last_otp(
    redis: RedisDep,
    settings: SettingsDep,
    email: str = Query(max_length=255),
    purpose: str = Query(pattern="^(login|register)$"),
):
    code = await redis.get(testing_otp_key(settings.key_prefix, email, purpose))
    if code is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Код не найден или истёк")
    return {"code": code}
