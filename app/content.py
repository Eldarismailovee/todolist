"""Работа с документом Tiptap.

Хранится структура (ProseMirror JSON), а не HTML: разметку рисует клиент, и на
сервер не попадает произвольный HTML, который потом пришлось бы чистить.
Из документа извлекается плоский текст — по нему строится полнотекстовый поиск.
"""

from typing import Any

# Глубина ограничена: вложенный документ мог бы уронить обход рекурсией.
MAX_DEPTH = 50
MAX_NODES = 20_000


class ContentError(ValueError):
    """Документ не похож на ProseMirror-структуру."""


def extract_text(document: Any) -> str:
    """Собирает текст всех текстовых узлов, разделяя блоки переводами строк."""
    if document is None:
        return ""
    if not isinstance(document, dict):
        raise ContentError("Ожидается объект документа")

    parts: list[str] = []
    nodes = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal nodes
        if depth > MAX_DEPTH:
            raise ContentError("Слишком глубокая вложенность документа")
        nodes += 1
        if nodes > MAX_NODES:
            raise ContentError("Слишком много узлов в документе")
        if not isinstance(node, dict):
            return

        if node.get("type") == "text":
            text = node.get("text")
            if isinstance(text, str):
                parts.append(text)
        # Подпись изображения тоже участвует в поиске.
        attrs = node.get("attrs")
        if isinstance(attrs, dict):
            for key in ("alt", "title"):
                value = attrs.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)

        children = node.get("content")
        if isinstance(children, list):
            for child in children:
                walk(child, depth + 1)
            if node.get("type") in {"paragraph", "heading", "listItem", "blockquote"}:
                parts.append("\n")

    walk(document, 0)
    return " ".join(" ".join(parts).split())
