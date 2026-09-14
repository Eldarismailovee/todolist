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


# --- Контракт документа --------------------------------------------------
# Набор узлов и marks описывает ровно те расширения, которые включены в
# редакторе: StarterKit (заголовки только 2 и 3 уровня), Image (inline=False)
# и Link. Ограничения глубины и числа узлов сами по себе контракта не задают:
# без списка допустимых узлов, атрибутов и схем URL в JSONB попадает что угодно.

INLINE_NODES = frozenset({"text", "hardBreak"})
BLOCK_NODES = frozenset(
    {
        "paragraph",
        "heading",
        "blockquote",
        "bulletList",
        "orderedList",
        "codeBlock",
        "horizontalRule",
        "image",
    }
)

# Схемы ссылок: javascript: и data: в href не допускаются вовсе.
LINK_SCHEMES = ("http://", "https://", "mailto:")
# Картинка — либо вложение этого приложения, либо внешний http(s)-адрес.
ATTACHMENT_PREFIX = "/api/v1/files/"


def _optional_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _heading_level(value: Any) -> bool:
    return value in (2, 3)


def _list_start(value: Any) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _link_href(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(LINK_SCHEMES)


def _image_src(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.startswith(ATTACHMENT_PREFIX) or value.startswith(("http://", "https://"))


# Узел: разрешённые атрибуты с проверкой значения, обязательные атрибуты и
# допустимые дочерние узлы (None — детей быть не может).
NODE_SPECS: dict[str, dict[str, Any]] = {
    "doc": {"attrs": {}, "required": (), "children": BLOCK_NODES},
    "paragraph": {"attrs": {}, "required": (), "children": INLINE_NODES},
    "heading": {
        "attrs": {"level": _heading_level},
        "required": ("level",),
        "children": INLINE_NODES,
    },
    "blockquote": {"attrs": {}, "required": (), "children": BLOCK_NODES},
    "bulletList": {"attrs": {}, "required": (), "children": frozenset({"listItem"})},
    "orderedList": {
        "attrs": {"start": _list_start, "type": _optional_string},
        "required": (),
        "children": frozenset({"listItem"}),
    },
    "listItem": {"attrs": {}, "required": (), "children": BLOCK_NODES},
    "codeBlock": {
        "attrs": {"language": _optional_string},
        "required": (),
        # Внутри кода только текст: marks там не размечаются.
        "children": frozenset({"text"}),
    },
    "horizontalRule": {"attrs": {}, "required": (), "children": None},
    "hardBreak": {"attrs": {}, "required": (), "children": None},
    "image": {
        "attrs": {"src": _image_src, "alt": _optional_string, "title": _optional_string},
        "required": ("src",),
        "children": None,
    },
    "text": {"attrs": {}, "required": (), "children": None},
}

MARK_SPECS: dict[str, dict[str, Any]] = {
    "bold": {"attrs": {}, "required": ()},
    "italic": {"attrs": {}, "required": ()},
    "strike": {"attrs": {}, "required": ()},
    "code": {"attrs": {}, "required": ()},
    "link": {
        "attrs": {
            "href": _link_href,
            "target": _optional_string,
            "rel": _optional_string,
            "class": _optional_string,
        },
        "required": ("href",),
    },
}


def _check_attrs(node_type: str, attrs: Any, spec: dict[str, Any], what: str) -> None:
    if attrs is None:
        attrs = {}
    if not isinstance(attrs, dict):
        raise ContentError(f"{what} {node_type}: attrs должен быть объектом")
    allowed: dict[str, Any] = spec["attrs"]
    for name, value in attrs.items():
        check = allowed.get(name)
        if check is None:
            raise ContentError(f"{what} {node_type}: неизвестный атрибут {name}")
        if not check(value):
            raise ContentError(f"{what} {node_type}: недопустимое значение атрибута {name}")
    for name in spec["required"]:
        if attrs.get(name) is None:
            raise ContentError(f"{what} {node_type}: обязателен атрибут {name}")


def _check_marks(marks: Any) -> None:
    if marks is None:
        return
    if not isinstance(marks, list):
        raise ContentError("marks должны быть списком")
    for mark in marks:
        if not isinstance(mark, dict):
            raise ContentError("marks: ожидается объект")
        mark_type = mark.get("type")
        spec = MARK_SPECS.get(mark_type) if isinstance(mark_type, str) else None
        if spec is None:
            raise ContentError(f"Неизвестный mark: {mark_type}")
        unexpected = set(mark) - {"type", "attrs"}
        if unexpected:
            raise ContentError(f"mark {mark_type}: лишние ключи {', '.join(sorted(unexpected))}")
        _check_attrs(mark_type, mark.get("attrs"), spec, "mark")


def validate_document(document: Any) -> None:
    """Проверяет структуру документа целиком. Ошибка — ContentError.

    Проверяются тип каждого узла, его атрибуты, допустимость вложения и схемы
    URL. Без этого в JSONB попадали любые узлы с любыми атрибутами: клиент
    рисует их у себя, и содержимое становится каналом для чужой разметки.
    """
    if document is None:
        return
    if not isinstance(document, dict):
        raise ContentError("Ожидается объект документа")
    if document.get("type") != "doc":
        raise ContentError("Корневой узел документа должен быть doc")

    nodes = 0

    def walk(node: Any, depth: int, allowed: frozenset[str]) -> None:
        nonlocal nodes
        if depth > MAX_DEPTH:
            raise ContentError("Слишком глубокая вложенность документа")
        nodes += 1
        if nodes > MAX_NODES:
            raise ContentError("Слишком много узлов в документе")
        if not isinstance(node, dict):
            raise ContentError("Узел документа должен быть объектом")

        node_type = node.get("type")
        if not isinstance(node_type, str) or node_type not in NODE_SPECS:
            raise ContentError(f"Неизвестный узел документа: {node_type}")
        if node_type not in allowed:
            raise ContentError(f"Узел {node_type} недопустим в этом месте документа")

        spec = NODE_SPECS[node_type]
        unexpected = set(node) - {"type", "attrs", "content", "marks", "text"}
        if unexpected:
            raise ContentError(f"Узел {node_type}: лишние ключи {', '.join(sorted(unexpected))}")
        _check_attrs(node_type, node.get("attrs"), spec, "Узел")

        if node_type == "text":
            if not isinstance(node.get("text"), str):
                raise ContentError("Текстовый узел без строки text")
        elif "text" in node:
            raise ContentError(f"Узел {node_type} не может содержать text")

        _check_marks(node.get("marks"))

        children = node.get("content")
        if children is None:
            return
        if spec["children"] is None:
            raise ContentError(f"Узел {node_type} не может иметь содержимого")
        if not isinstance(children, list):
            raise ContentError(f"Узел {node_type}: content должен быть списком")
        for child in children:
            walk(child, depth + 1, spec["children"])

    walk(document, 0, frozenset({"doc"}))
