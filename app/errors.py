"""Ошибки валидации с указанием поля.

FastAPI описывает отказ валидации списком объектов `{loc, msg, type}`, и клиент
раскладывает их по полям формы именно по `loc`. Бизнес-проверка, отвечающая
строкой, такой возможности не даёт: сообщение «Категория не найдена» можно
показать только общим текстом, а поле останется без пометки.
"""

from fastapi import HTTPException, status


def field_error(
    loc: list[str | int],
    msg: str,
    error_type: str = "value_error",
) -> HTTPException:
    """422 с путём поля внутри тела запроса."""
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", *loc], "msg": msg, "type": error_type}],
    )
