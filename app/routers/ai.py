"""AI-ассистент: идеи, суммаризация, декомпозиция, правка текста."""

import logging

import anthropic
from fastapi import APIRouter, HTTPException, Request, status

from ..dependencies import AssistantDep, DetachedPrincipal, RedisDep, SettingsDep
from ..integrations.ai import AssistantRefused
from ..schemas import AssistRequest, AssistResponse
from ..security import enforce_rate_limit

router = APIRouter(prefix="/ai", tags=["ai"])
logger = logging.getLogger(__name__)


@router.post("/assist", response_model=AssistResponse)
async def assist(
    payload: AssistRequest,
    request: Request,
    principal: DetachedPrincipal,
    assistant: AssistantDep,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Обращение к модели платное, поэтому лимит считается по пользователю.

    Сессия проверяется короткой отдельной сессией БД: ожидание внешней
    модели не должно удерживать SQL-соединение из пула.
    """
    await enforce_rate_limit(
        redis,
        settings,
        f"ai:{principal.user_id}",
        settings.ai_rate_limit,
        settings.ai_rate_window_seconds,
    )

    try:
        reply = await assistant.run(payload.action, payload.text)
    except AssistantRefused as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except anthropic.RateLimitError as error:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Лимит запросов к модели исчерпан"
        ) from error
    except anthropic.APIStatusError as error:
        logger.warning("AI-провайдер вернул %s", error.status_code)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ассистент временно недоступен") from error
    except anthropic.APIConnectionError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ассистент временно недоступен") from error

    return AssistResponse(
        text=reply.text, items=reply.items, provider=reply.provider, model=reply.model
    )
