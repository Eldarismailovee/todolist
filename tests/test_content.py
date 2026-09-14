"""Контракт документа Tiptap: допустимые узлы, атрибуты и схемы URL."""

import pytest

from app.content import ContentError, validate_document

from .conftest import create_project, register, set_metadata


def _doc(*nodes: dict) -> dict:
    return {"type": "doc", "content": list(nodes)}


def _paragraph(text: str = "текст", **extra) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text, **extra}]}


# Снято с настоящей схемы редактора (StarterKit + Image + Link, заголовки 2–3):
# контракт должен описывать то, что клиент действительно отправляет, а не то,
# что кажется разумным. Атрибуты приведены как есть, вместе с null-значениями.
EDITOR_OUTPUT = {
    "type": "doc",
    "content": [
        {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "Заголовок"}],
        },
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "marks": [{"type": "bold"}], "text": "жирный"},
                {"type": "text", "marks": [{"type": "italic"}], "text": "курсив"},
                {
                    "type": "text",
                    "marks": [
                        {
                            "type": "link",
                            "attrs": {
                                "href": "https://example.com",
                                "target": "_blank",
                                "rel": "noopener noreferrer nofollow",
                                "class": None,
                            },
                        }
                    ],
                    "text": "ссылка",
                },
                {"type": "hardBreak"},
                {"type": "text", "marks": [{"type": "code"}], "text": "код"},
                {"type": "text", "marks": [{"type": "strike"}], "text": "зачёркнуто"},
            ],
        },
        {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": []}]},
            ],
        },
        {
            "type": "orderedList",
            "attrs": {"start": 3, "type": None},
            "content": [{"type": "listItem", "content": [{"type": "paragraph"}]}],
        },
        {"type": "blockquote", "content": [{"type": "paragraph"}]},
        {
            "type": "codeBlock",
            "attrs": {"language": "python"},
            "content": [{"type": "text", "text": "x=1"}],
        },
        {"type": "horizontalRule"},
        {"type": "image", "attrs": {"src": "/api/v1/files/abc", "alt": "схема", "title": None}},
    ],
}

VALID_DOCUMENTS = [
    EDITOR_OUTPUT,
    _doc(_paragraph()),
    _doc(
        {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "Заголовок"}],
        }
    ),
    _doc(
        {
            "type": "bulletList",
            "content": [{"type": "listItem", "content": [_paragraph("пункт")]}],
        }
    ),
    _doc({"type": "orderedList", "attrs": {"start": 3}, "content": [{"type": "listItem"}]}),
    _doc(
        {
            "type": "codeBlock",
            "attrs": {"language": "python"},
            "content": [{"type": "text", "text": "x=1"}],
        }
    ),
    _doc({"type": "horizontalRule"}),
    _doc({"type": "image", "attrs": {"src": "/api/v1/files/abc", "alt": "схема"}}),
    _doc({"type": "image", "attrs": {"src": "https://example.com/a.png", "title": None}}),
    _doc(_paragraph("жирный", marks=[{"type": "bold"}])),
    _doc(_paragraph("ссылка", marks=[{"type": "link", "attrs": {"href": "https://example.com"}}])),
    _doc({"type": "paragraph", "content": [{"type": "hardBreak"}]}),
]

INVALID_DOCUMENTS = [
    ({"type": "paragraph"}, "корень не doc"),
    (_doc({"type": "script", "content": []}), "неизвестный узел"),
    (_doc({"type": "heading", "attrs": {"level": 1}}), "уровень вне настроек редактора"),
    (_doc({"type": "heading"}), "обязательный атрибут отсутствует"),
    (_doc({"type": "paragraph", "attrs": {"onclick": "alert(1)"}}), "неизвестный атрибут"),
    (_doc({"type": "image", "attrs": {"src": "javascript:alert(1)"}}), "схема javascript"),
    (_doc({"type": "image", "attrs": {"src": "data:image/svg+xml,<svg/>"}}), "схема data"),
    (
        _doc(
            _paragraph("ссылка", marks=[{"type": "link", "attrs": {"href": "javascript:alert(1)"}}])
        ),
        "javascript в href",
    ),
    (_doc(_paragraph("текст", marks=[{"type": "blink"}])), "неизвестный mark"),
    (_doc({"type": "listItem", "content": []}), "listItem вне списка"),
    (_doc({"type": "horizontalRule", "content": [_paragraph()]}), "содержимое у пустого узла"),
    (_doc({"type": "paragraph", "content": [{"type": "text"}]}), "текстовый узел без text"),
    (_doc({"type": "paragraph", "text": "мимо"}), "text у нетекстового узла"),
    (_doc({"type": "paragraph", "unknown": 1}), "лишний ключ узла"),
]


@pytest.mark.parametrize("document", VALID_DOCUMENTS)
def test_editor_documents_are_accepted(document):
    validate_document(document)


@pytest.mark.parametrize(("document", "case"), INVALID_DOCUMENTS)
def test_documents_outside_the_contract_are_rejected(document, case):
    with pytest.raises(ContentError):
        validate_document(document)


def test_empty_document_is_allowed():
    validate_document(None)
    validate_document({"type": "doc"})


async def test_task_with_foreign_node_is_rejected_with_field_loc(client):
    await register(client, "content-contract@example.com")
    await set_metadata([])
    project_id = await create_project(client)

    response = await client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Задача",
            "content": _doc({"type": "iframe", "attrs": {"src": "https://example.com"}}),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "content"]
