"""Поиск, теги и категории, вложения, ассистент, аналитика, уведомления."""

import io
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal
from app.models import Task, TaskNotification

from .conftest import bearer, create_project, create_task, fresh_access, register

settings = get_settings()

RICH_CONTENT = {
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": "Позвонить подрядчику про плитку"}],
        }
    ],
}


# --- Поиск ---------------------------------------------------------------


async def test_full_text_search_finds_by_title_and_content(client):
    await register(client, "search@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Купить ламинат")
    await create_task(client, project_id, "Созвон", content=RICH_CONTENT)
    await create_task(client, project_id, "Отпуск")

    by_title = await client.get(
        "/api/v1/tasks/search?q=ламинат", headers=bearer(await fresh_access(client))
    )
    by_content = await client.get(
        "/api/v1/tasks/search?q=подрядчику", headers=bearer(await fresh_access(client))
    )

    assert [task["title"] for task in by_title.json()] == ["Купить ламинат"]
    # Текст ищется внутри документа редактора, а не только в заголовке.
    assert [task["title"] for task in by_content.json()] == ["Созвон"]


async def test_search_matches_word_forms(client):
    """Русская морфология: «задачи» находит «задача»."""
    await register(client, "morph@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Важная задача")

    response = await client.get(
        "/api/v1/tasks/search?q=задачи", headers=bearer(await fresh_access(client))
    )

    assert [task["title"] for task in response.json()] == ["Важная задача"]


async def test_search_does_not_leak_other_users(client):
    from .test_isolation import new_client

    await register(client, "mine@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Секретная встреча")

    async with new_client() as other:
        await register(other, "notmine@example.com")
        response = await other.get(
            "/api/v1/tasks/search?q=Секретная", headers=bearer(await fresh_access(other))
        )

    assert response.json() == []


async def test_search_input_with_punctuation_is_safe(client):
    """websearch_to_tsquery принимает произвольный ввод без экранирования."""
    await register(client, "punct@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Отчёт за квартал")

    response = await client.get(
        "/api/v1/tasks/search?q=%22отчёт%22 -квартал!", headers=bearer(await fresh_access(client))
    )

    assert response.status_code == 200


# --- Теги и категории ----------------------------------------------------


async def test_tags_and_category_filtering(client):
    await register(client, "tags@example.com")
    project_id = await create_project(client)

    urgent = (
        await client.post(
            "/api/v1/tags", json={"name": "срочно"}, headers=bearer(await fresh_access(client))
        )
    ).json()
    home = (
        await client.post(
            "/api/v1/tags", json={"name": "дом"}, headers=bearer(await fresh_access(client))
        )
    ).json()
    work = (
        await client.post(
            "/api/v1/categories",
            json={"name": "Работа"},
            headers=bearer(await fresh_access(client)),
        )
    ).json()

    await create_task(client, project_id, "Обе метки", tag_ids=[urgent["id"], home["id"]])
    await create_task(client, project_id, "Одна метка", tag_ids=[urgent["id"]])
    await create_task(client, project_id, "С категорией", category_id=work["id"])

    both = await client.get(
        f"/api/v1/tasks?project_id={project_id}&tag_id={urgent['id']}&tag_id={home['id']}",
        headers=bearer(await fresh_access(client)),
    )
    by_category = await client.get(
        f"/api/v1/tasks?project_id={project_id}&category_id={work['id']}",
        headers=bearer(await fresh_access(client)),
    )

    # Несколько тегов сужают выборку, а не расширяют её.
    assert [task["title"] for task in both.json()] == ["Обе метки"]
    assert [task["title"] for task in by_category.json()] == ["С категорией"]


async def test_duplicate_tag_rejected(client):
    await register(client, "duptag@example.com")
    await client.post(
        "/api/v1/tags", json={"name": "дом"}, headers=bearer(await fresh_access(client))
    )
    duplicate = await client.post(
        "/api/v1/tags", json={"name": "дом"}, headers=bearer(await fresh_access(client))
    )
    assert duplicate.status_code == 409


async def test_foreign_tag_cannot_be_attached(client):
    from .test_isolation import new_client

    await register(client, "tagowner@example.com")
    tag = (
        await client.post(
            "/api/v1/tags", json={"name": "чужой"}, headers=bearer(await fresh_access(client))
        )
    ).json()

    async with new_client() as other:
        await register(other, "tagthief@example.com")
        project_id = await create_project(other)
        response = await other.post(
            "/api/v1/tasks",
            json={"project_id": project_id, "title": "Задача", "tag_ids": [tag["id"]]},
            headers=bearer(await fresh_access(other)),
        )

    assert response.status_code == 422


# --- Вложения ------------------------------------------------------------


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


async def test_image_upload_and_signed_download(client):
    await register(client, "upload@example.com")

    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
        headers=bearer(await fresh_access(client)),
    )

    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["content_type"] == "image/png"
    # Ссылка подписана: тег <img> не может отправить заголовок Authorization.
    assert "sig=" in body["url"] and "exp=" in body["url"]

    # Скачивание по подписанной ссылке идёт без токена.
    downloaded = await client.get(body["url"])
    assert downloaded.status_code == 200
    assert downloaded.content == PNG


async def test_download_without_signature_or_token_is_denied(client):
    await register(client, "nosig@example.com")
    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
        headers=bearer(await fresh_access(client)),
    )
    file_id = uploaded.json()["id"]

    response = await client.get(f"/api/v1/files/{file_id}")

    assert response.status_code == 404


async def test_tampered_signature_is_rejected(client):
    await register(client, "tamper@example.com")
    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
        headers=bearer(await fresh_access(client)),
    )
    url = uploaded.json()["url"]

    response = await client.get(url.replace("sig=", "sig=x"))

    assert response.status_code == 404


async def test_unsupported_type_rejected(client):
    await register(client, "badtype@example.com")

    response = await client.post(
        "/api/v1/files",
        files={"file": ("payload.html", io.BytesIO(b"<script>"), "text/html")},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 415


async def test_content_stores_bare_path_and_returns_signed(client):
    """Подпись живёт час, поэтому в JSONB она не сохраняется."""
    await register(client, "signed@example.com")
    project_id = await create_project(client)
    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
        headers=bearer(await fresh_access(client)),
    )
    signed_url = uploaded.json()["url"]

    document = {
        "type": "doc",
        "content": [{"type": "image", "attrs": {"src": signed_url, "alt": "точка"}}],
    }
    task = await create_task(client, project_id, "С картинкой", content=document)

    assert "sig=" in task["content"]["content"][0]["attrs"]["src"]
    async with SessionLocal() as session:
        stored = await session.scalar(select(Task).where(Task.id == task["id"]))
        src = stored.content["content"][0]["attrs"]["src"]
    assert "sig=" not in src and src.startswith("/api/v1/files/")


def _document(src: str) -> dict:
    return {"type": "doc", "content": [{"type": "image", "attrs": {"src": src, "alt": "точка"}}]}


async def _upload_by_stranger(email: str) -> str:
    """Файл чужого пользователя; возвращает его UUID."""
    from .test_isolation import new_client

    async with new_client() as stranger:
        await register(stranger, email)
        uploaded = await stranger.post(
            "/api/v1/files",
            files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
            headers=bearer(await fresh_access(stranger)),
        )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()["id"]


async def test_foreign_attachment_in_content_is_rejected(client):
    """Ссылка на чужой файл не должна превращаться в подпись к нему.

    Подпись даёт скачивание без сессии, поэтому документ — не способ получить
    доступ, которого у владельца задачи нет.
    """
    foreign_id = await _upload_by_stranger("filethief-victim@example.com")
    await register(client, "filethief@example.com")
    project_id = await create_project(client)

    created = await client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Чужая картинка",
            "content": _document(f"/api/v1/files/{foreign_id}"),
        },
        headers=bearer(await fresh_access(client)),
    )

    assert created.status_code == 422, created.text

    task = await create_task(client, project_id, "Своя задача")
    patched = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"content": _document(f"/api/v1/files/{foreign_id}")},
        headers=bearer(await fresh_access(client)),
    )

    assert patched.status_code == 422, patched.text
    # Файл по-прежнему недоступен без сессии владельца.
    assert (await client.get(f"/api/v1/files/{foreign_id}")).status_code == 404


async def test_stored_foreign_attachment_is_never_signed(client):
    """Документ, сохранённый до проверки на записи, подписи не получает."""
    foreign_id = await _upload_by_stranger("oldlink-victim@example.com")
    await register(client, "oldlink@example.com")
    project_id = await create_project(client)
    task = await create_task(client, project_id, "Старая задача")

    async with SessionLocal() as session:
        await session.execute(
            update(Task)
            .where(Task.id == task["id"])
            .values(content=_document(f"/api/v1/files/{foreign_id}"))
        )
        await session.commit()

    listing = await client.get(
        f"/api/v1/tasks?project_id={project_id}", headers=bearer(await fresh_access(client))
    )

    src = listing.json()[0]["content"]["content"][0]["attrs"]["src"]
    assert src == f"/api/v1/files/{foreign_id}"
    assert (await client.get(src)).status_code == 404


async def test_replay_does_not_hand_out_foreign_signature(client, redis_client):
    """Повтор по Idempotency-Key подписывает заново, а не отдаёт кэш как есть."""
    foreign_id = await _upload_by_stranger("replay-victim@example.com")
    await register(client, "replay@example.com")
    project_id = await create_project(client)
    body = {"project_id": project_id, "title": "Повтор"}
    headers = {**bearer(await fresh_access(client)), "Idempotency-Key": "replay-key-0001"}

    first = await client.post("/api/v1/tasks", json=body, headers=headers)
    assert first.status_code == 201, first.text

    # Кэш ответа мог быть записан до проверки владельца: подменяем его так,
    # как выглядела бы запись со старой ссылкой.
    keys = [key async for key in redis_client.scan_iter(match=f"{settings.key_prefix}idem:*")]
    assert len(keys) == 1
    record = json.loads(await redis_client.get(keys[0]))
    record["response"]["content"] = _document(f"/api/v1/files/{foreign_id}")
    await redis_client.set(keys[0], json.dumps(record))

    replayed = await client.post(
        "/api/v1/tasks",
        json=body,
        headers={**bearer(await fresh_access(client)), "Idempotency-Key": "replay-key-0001"},
    )

    assert replayed.status_code == 201, replayed.text
    src = replayed.json()["content"]["content"][0]["attrs"]["src"]
    assert "sig=" not in src
    assert (await client.get(src)).status_code == 404


async def test_own_attachment_is_signed_on_read_and_downloads(client):
    """Регресс наоборот: собственный файл продолжает открываться."""
    await register(client, "ownfile@example.com")
    project_id = await create_project(client)
    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
        headers=bearer(await fresh_access(client)),
    )
    await create_task(client, project_id, "С картинкой", content=_document(uploaded.json()["url"]))

    listing = await client.get(
        f"/api/v1/tasks?project_id={project_id}", headers=bearer(await fresh_access(client))
    )

    src = listing.json()[0]["content"]["content"][0]["attrs"]["src"]
    assert "sig=" in src
    downloaded = await client.get(src)
    assert downloaded.status_code == 200
    assert downloaded.content == PNG


# --- AI-ассистент --------------------------------------------------------


async def test_assistant_decomposes_task(client):
    await register(client, "ai@example.com")

    response = await client.post(
        "/api/v1/ai/assist",
        json={"action": "decompose", "text": "Организовать переезд офиса"},
        headers=bearer(await fresh_access(client)),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) >= 2
    # Без ключа ответ помечен как заглушка и не выдаётся за работу модели.
    assert body["provider"] == "stub"


async def test_assistant_rejects_unknown_action(client):
    await register(client, "ai-bad@example.com")
    response = await client.post(
        "/api/v1/ai/assist",
        json={"action": "translate", "text": "..."},
        headers=bearer(await fresh_access(client)),
    )
    assert response.status_code == 422


async def test_assistant_requires_authentication(client):
    response = await client.post(
        "/api/v1/ai/assist", json={"action": "ideas", "text": "что-нибудь"}
    )
    assert response.status_code == 401


# --- Аналитика -----------------------------------------------------------


async def test_analytics_counts_tasks_and_completion(client):
    await register(client, "stats@example.com")
    project_id = await create_project(client)
    columns = (
        await client.get(
            f"/api/v1/board/columns?project_id={project_id}",
            headers=bearer(await fresh_access(client)),
        )
    ).json()
    done_column = columns[-1]["id"]

    await create_task(client, project_id, "Открытая")
    finished = await create_task(client, project_id, "Закрытая")
    await client.post(
        f"/api/v1/tasks/{finished['id']}/move",
        json={"column_id": done_column},
        headers=bearer(await fresh_access(client)),
    )
    await create_task(
        client,
        project_id,
        "Просроченная",
        due_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
    )

    response = await client.get(
        "/api/v1/analytics/summary?days=7", headers=bearer(await fresh_access(client))
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["completed"] == 1
    assert body["overdue"] == 1
    assert body["completion_rate"] == round(1 / 3, 4)
    assert len(body["daily"]) == 7
    assert sum(point["created"] for point in body["daily"]) == 3


async def test_analytics_ignores_other_users(client):
    from .test_isolation import new_client

    await register(client, "stats-mine@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Моя")

    async with new_client() as other:
        await register(other, "stats-other@example.com")
        response = await other.get(
            "/api/v1/analytics/summary", headers=bearer(await fresh_access(other))
        )

    assert response.json()["total"] == 0


# --- Уведомления ---------------------------------------------------------


async def test_notification_settings_round_trip(client):
    await register(client, "notify@example.com")

    defaults = await client.get(
        "/api/v1/notifications/settings", headers=bearer(await fresh_access(client))
    )
    updated = await client.put(
        "/api/v1/notifications/settings",
        json={
            "email_enabled": True,
            "telegram_enabled": True,
            "telegram_chat_id": "123456789",
            "lead_time_minutes": 120,
        },
        headers=bearer(await fresh_access(client)),
    )

    assert defaults.json()["email_enabled"] is True
    assert updated.status_code == 200
    assert updated.json()["lead_time_minutes"] == 120


async def test_telegram_requires_chat_id(client):
    await register(client, "notg@example.com")
    response = await client.put(
        "/api/v1/notifications/settings",
        json={
            "email_enabled": True,
            "telegram_enabled": True,
            "telegram_chat_id": None,
            "lead_time_minutes": 60,
        },
        headers=bearer(await fresh_access(client)),
    )
    assert response.status_code == 422


async def test_worker_sends_reminder_once(client):
    """Отметка об отправке не даёт слать одно и то же каждую минуту."""
    from app import worker

    await register(client, "deadline@example.com")
    project_id = await create_project(client)
    soon = datetime.now(UTC) + timedelta(minutes=30)
    task = await create_task(client, project_id, "Скоро срок", due_at=soon.isoformat())

    first = await worker.run_once()
    second = await worker.run_once()

    assert first == 1
    assert second == 0
    async with SessionLocal() as session:
        records = (
            await session.scalars(
                select(TaskNotification).where(TaskNotification.task_id == task["id"])
            )
        ).all()
    assert [record.kind for record in records] == ["due_soon"]
    assert "email" in records[0].channels


async def test_worker_skips_completed_and_distant_tasks(client):
    from app import worker

    await register(client, "quiet@example.com")
    project_id = await create_project(client)
    await create_task(
        client,
        project_id,
        "Далёкий срок",
        due_at=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
    )
    finished = await create_task(
        client,
        project_id,
        "Уже сделано",
        due_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    await client.patch(
        f"/api/v1/tasks/{finished['id']}",
        json={"completed": True},
        headers=bearer(await fresh_access(client)),
    )

    assert await worker.run_once() == 0


async def test_worker_reports_overdue_separately(client):
    from app import worker

    await register(client, "overdue@example.com")
    project_id = await create_project(client)
    await create_task(
        client,
        project_id,
        "Просрочена",
        due_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
    )

    assert await worker.run_once() == 1
    async with SessionLocal() as session:
        kinds = (await session.scalars(select(TaskNotification.kind))).all()
    assert list(kinds) == ["overdue"]
