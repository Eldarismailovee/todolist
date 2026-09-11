"""AI-ассистент поверх Claude.

Без ключа работает детерминированная заглушка: интерфейс и контракт ответа те
же, поэтому UI и тесты не зависят от наличия ключа. Заглушка честно помечает
себя в поле `provider`, чтобы её вывод нельзя было принять за ответ модели.
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal

import anthropic

from ..config import Settings

logger = logging.getLogger(__name__)

Action = Literal["ideas", "summarize", "decompose", "rewrite"]

PROMPTS: dict[Action, str] = {
    "ideas": (
        "Предложи 5 конкретных идей или следующих шагов по теме пользователя. "
        "Каждый пункт — с новой строки, без нумерации и вступления."
    ),
    "summarize": (
        "Сожми текст пользователя до 3–5 ключевых пунктов. "
        "Каждый пункт — с новой строки, без вступления."
    ),
    "decompose": (
        "Разбей задачу пользователя на 3–7 подзадач, каждая — законченное "
        "действие. Каждая подзадача с новой строки, без нумерации и вступления."
    ),
    "rewrite": (
        "Перепиши текст пользователя грамотно и понятно, сохранив смысл, язык "
        "и примерный объём. Верни только исправленный текст."
    ),
}

SYSTEM = (
    "Ты помощник в приложении для задач. Отвечай на языке пользователя, кратко "
    "и по существу, без markdown-разметки и без вводных фраз."
)


@dataclass(slots=True)
class AssistantReply:
    text: str
    items: list[str]
    provider: str
    model: str | None = None


def _split_items(text: str) -> list[str]:
    """Строки ответа как список: нумерация и маркеры списка убираются."""
    items = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items


class Assistant:
    async def run(self, action: Action, text: str) -> AssistantReply:  # pragma: no cover
        raise NotImplementedError


@dataclass
class StubAssistant(Assistant):
    """Заглушка без обращения к модели.

    Она не выдаёт себя за ИИ: возвращает предсказуемые заготовки и
    provider="stub", чтобы интерфейс мог показать предупреждение.
    """

    async def run(self, action: Action, text: str) -> AssistantReply:
        excerpt = " ".join(text.split())[:120]
        if action == "decompose":
            items = [
                f"Уточнить требования: {excerpt}" if excerpt else "Уточнить требования",
                "Наметить план и оценить сроки",
                "Выполнить основную часть работы",
                "Проверить результат",
            ]
        elif action == "ideas":
            items = [
                "Разбить задачу на шаги и начать с самого короткого",
                "Назначить срок и напоминание",
                "Отметить блокирующие зависимости",
                "Определить признак готовности",
            ]
        elif action == "summarize":
            sentences = [part.strip() for part in re.split(r"[.!?\n]+", text) if part.strip()]
            items = sentences[:5] or ["Текст пуст"]
        else:
            items = [text.strip() or "Текст пуст"]
        return AssistantReply(text="\n".join(items), items=items, provider="stub", model=None)


@dataclass
class ClaudeAssistant(Assistant):
    settings: Settings

    def __post_init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=self.settings.anthropic_api_key)

    async def run(self, action: Action, text: str) -> AssistantReply:
        response = await self._client.messages.create(
            model=self.settings.ai_model,
            max_tokens=self.settings.ai_max_tokens,
            system=SYSTEM,
            # Мышление адаптивное по умолчанию; задачи простые, поэтому низкое
            # усилие вместо отключения мышления.
            output_config={"effort": self.settings.ai_effort},
            messages=[
                {
                    "role": "user",
                    "content": f"{PROMPTS[action]}\n\nТекст пользователя:\n{text}",
                }
            ],
        )

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            logger.info("Модель отклонила запрос ассистента (%s)", category)
            raise AssistantRefused("Модель отклонила этот запрос")

        answer = "\n".join(block.text for block in response.content if block.type == "text").strip()
        return AssistantReply(
            text=answer,
            items=_split_items(answer),
            provider="anthropic",
            model=response.model,
        )


class AssistantRefused(Exception):
    """Модель отказалась отвечать — это не сбой сервиса."""


class AssistantUnavailable(Exception):
    """Провайдер недоступен: сеть, лимиты, ошибка авторизации ключа."""


def create_assistant(settings: Settings) -> Assistant:
    return ClaudeAssistant(settings) if settings.anthropic_api_key else StubAssistant()
