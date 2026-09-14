"""Поиск, теги и категории, вложения, ассистент, аналитика, уведомления."""

import io
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal
from app.models import Task, TaskNotification

from .conftest import PNG, create_project, create_task, register

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

    by_title = await client.get("/api/v1/tasks/search?q=ламинат")
    by_content = await client.get("/api/v1/tasks/search?q=подрядчику")

    assert [task["title"] for task in by_title.json()] == ["Купить ламинат"]
    # Текст ищется внутри документа редактора, а не только в заголовке.
    assert [task["title"] for task in by_content.json()] == ["Созвон"]


async def test_search_matches_word_forms(client):
    """Русская морфология: «задачи» находит «задача»."""
    await register(client, "morph@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Важная задача")

    response = await client.get("/api/v1/tasks/search?q=задачи")

    assert [task["title"] for task in response.json()] == ["Важная задача"]


async def test_search_does_not_leak_other_users(client):
    from .test_isolation import new_client

    await register(client, "mine@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Секретная встреча")

    async with new_client() as other:
        await register(other, "notmine@example.com")
        response = await other.get("/api/v1/tasks/search?q=Секретная")

    assert response.json() == []


async def test_search_input_with_punctuation_is_safe(client):
    """websearch_to_tsquery принимает произвольный ввод без экранирования."""
    await register(client, "punct@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Отчёт за квартал")

    response = await client.get("/api/v1/tasks/search?q=%22отчёт%22 -квартал!")

    assert response.status_code == 200


# --- Теги и категории ----------------------------------------------------


async def test_tags_and_category_filtering(client):
    await register(client, "tags@example.com")
    project_id = await create_project(client)

    urgent = (await client.post("/api/v1/tags", json={"name": "срочно"})).json()
    home = (await client.post("/api/v1/tags", json={"name": "дом"})).json()
    work = (
        await client.post(
            "/api/v1/categories",
            json={"name": "Работа"},
        )
    ).json()

    await create_task(client, project_id, "Обе метки", tag_ids=[urgent["id"], home["id"]])
    await create_task(client, project_id, "Одна метка", tag_ids=[urgent["id"]])
    await create_task(client, project_id, "С категорией", category_id=work["id"])

    both = await client.get(
        f"/api/v1/tasks?project_id={project_id}&tag_id={urgent['id']}&tag_id={home['id']}"
    )
    by_category = await client.get(
        f"/api/v1/tasks?project_id={project_id}&category_id={work['id']}"
    )

    # Несколько тегов сужают выборку, а не расширяют её.
    assert [task["title"] for task in both.json()] == ["Обе метки"]
    assert [task["title"] for task in by_category.json()] == ["С категорией"]


async def test_duplicate_tag_rejected(client):
    await register(client, "duptag@example.com")
    await client.post("/api/v1/tags", json={"name": "дом"})
    duplicate = await client.post("/api/v1/tags", json={"name": "дом"})
    assert duplicate.status_code == 409


async def test_foreign_tag_cannot_be_attached(client):
    from .test_isolation import new_client

    await register(client, "tagowner@example.com")
    tag = (await client.post("/api/v1/tags", json={"name": "чужой"})).json()

    async with new_client() as other:
        await register(other, "tagthief@example.com")
        project_id = await create_project(other)
        response = await other.post(
            "/api/v1/tasks",
            json={"project_id": project_id, "title": "Задача", "tag_ids": [tag["id"]]},
        )

    assert response.status_code == 422


# --- Вложения ------------------------------------------------------------


async def test_image_upload_and_signed_download(client):
    await register(client, "upload@example.com")

    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
    )

    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["content_type"] == "image/png"
    # Обычный путь без подписи: картинку авторизует сессионная cookie, которую
    # тег <img> отправляет сам.
    assert body["url"] == f"/api/v1/files/{body['id']}"

    downloaded = await client.get(body["url"])
    assert downloaded.status_code == 200
    assert downloaded.content == PNG


async def test_download_without_a_session_is_denied(client):
    """Ссылка на файл больше не является предъявительским доступом."""
    await register(client, "nosession@example.com")
    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
    )
    url = uploaded.json()["url"]
    client.cookies.clear()

    response = await client.get(url)

    assert response.status_code == 401


async def test_foreign_file_is_not_downloadable(client):
    """Для чужого файла ответ такой же, как для отсутствующего."""
    foreign_id = await _upload_by_stranger("download-victim@example.com")
    await register(client, "download-thief@example.com")

    response = await client.get(f"/api/v1/files/{foreign_id}")

    assert response.status_code == 404


async def test_unsupported_type_rejected(client):
    await register(client, "badtype@example.com")

    response = await client.post(
        "/api/v1/files",
        files={"file": ("payload.html", io.BytesIO(b"<script>"), "text/html")},
    )

    assert response.status_code == 415


async def test_content_stores_a_bare_path(client):
    """В JSONB оседает путь без параметров, что бы ни прислал клиент."""
    await register(client, "bare-path@example.com")
    project_id = await create_project(client)
    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
    )
    file_id = uploaded.json()["id"]

    document = {
        "type": "doc",
        "content": [
            {
                "type": "image",
                "attrs": {"src": f"/api/v1/files/{file_id}?exp=1&sig=x", "alt": "точка"},
            }
        ],
    }
    task = await create_task(client, project_id, "С картинкой", content=document)

    async with SessionLocal() as session:
        stored = await session.scalar(select(Task).where(Task.id == task["id"]))
        src = stored.content["content"][0]["attrs"]["src"]
    assert src == f"/api/v1/files/{file_id}"
    assert task["content"]["content"][0]["attrs"]["src"] == src


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
        )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()["id"]


async def test_foreign_attachment_in_content_is_rejected(client):
    """Ссылка на чужой файл отвергается на записи.

    Выдача и так проверяет владельца, но молча сохранённая ссылка означала бы
    неработающую картинку в задаче без всякого объяснения.
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
    )

    assert created.status_code == 422, created.text

    task = await create_task(client, project_id, "Своя задача")
    patched = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"content": _document(f"/api/v1/files/{foreign_id}")},
    )

    assert patched.status_code == 422, patched.text
    # Файл по-прежнему недоступен без сессии владельца.
    assert (await client.get(f"/api/v1/files/{foreign_id}")).status_code == 404


async def test_stored_foreign_attachment_stays_inaccessible(client):
    """Документ, сохранённый до проверки на записи, доступа к файлу не даёт."""
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

    listing = await client.get(f"/api/v1/tasks?project_id={project_id}")

    src = listing.json()[0]["content"]["content"][0]["attrs"]["src"]
    assert src == f"/api/v1/files/{foreign_id}"
    assert (await client.get(src)).status_code == 404


async def test_replay_does_not_hand_out_access_to_a_foreign_file(client, redis_client):
    """Повторный ответ по Idempotency-Key не является доступом к файлу."""
    foreign_id = await _upload_by_stranger("replay-victim@example.com")
    await register(client, "replay@example.com")
    project_id = await create_project(client)
    body = {"project_id": project_id, "title": "Повтор"}
    headers = {"Idempotency-Key": "replay-key-0001"}

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
        headers={"Idempotency-Key": "replay-key-0001"},
    )

    assert replayed.status_code == 201, replayed.text
    src = replayed.json()["content"]["content"][0]["attrs"]["src"]
    # Путь в ответе есть, но он ничего не открывает: доступ проверяет выдача.
    assert (await client.get(src)).status_code == 404


async def test_own_attachment_stays_downloadable(client):
    """Регресс наоборот: собственный файл продолжает открываться."""
    await register(client, "ownfile@example.com")
    project_id = await create_project(client)
    uploaded = await client.post(
        "/api/v1/files",
        files={"file": ("dot.png", io.BytesIO(PNG), "image/png")},
    )
    await create_task(client, project_id, "С картинкой", content=_document(uploaded.json()["url"]))

    listing = await client.get(f"/api/v1/tasks?project_id={project_id}")

    src = listing.json()[0]["content"]["content"][0]["attrs"]["src"]
    downloaded = await client.get(src)
    assert downloaded.status_code == 200
    assert downloaded.content == PNG


# --- AI-ассистент --------------------------------------------------------


async def test_assistant_decomposes_task(client):
    await register(client, "ai@example.com")

    response = await client.post(
        "/api/v1/ai/assist",
        json={"action": "decompose", "text": "Организовать переезд офиса"},
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
    columns = (await client.get(f"/api/v1/board/columns?project_id={project_id}")).json()
    done_column = columns[-1]["id"]

    await create_task(client, project_id, "Открытая")
    finished = await create_task(client, project_id, "Закрытая")
    await client.post(
        f"/api/v1/tasks/{finished['id']}/move",
        json={"column_id": done_column},
    )
    await create_task(
        client,
        project_id,
        "Просроченная",
        due_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
    )

    response = await client.get("/api/v1/analytics/summary?days=7")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["completed"] == 1
    assert body["overdue"] == 1
    assert body["completion_rate"] == round(1 / 3, 4)
    assert len(body["daily"]) == 7
    assert sum(point["created"] for point in body["daily"]) == 3


async def test_analytics_first_day_starts_at_midnight(client):
    """Граница диапазона — начало дня, а не «столько же времени назад».

    Задача, созданная сегодня ночью, попадает в сегодняшний столбец: при
    отсчёте от текущего времени суток она выпадала из выборки, хотя её дата
    в диапазоне.
    """
    await register(client, "stats-midnight@example.com")
    project_id = await create_project(client)
    task = await create_task(client, project_id, "Ночная")

    midnight = datetime.now(UTC).replace(hour=0, minute=1, second=0, microsecond=0)
    async with SessionLocal() as session:
        await session.execute(update(Task).where(Task.id == task["id"]).values(created_at=midnight))
        await session.commit()

    body = (await client.get("/api/v1/analytics/summary?days=1")).json()

    assert len(body["daily"]) == 1
    assert body["daily"][0]["date"] == midnight.date().isoformat()
    assert body["daily"][0]["created"] == 1


async def test_analytics_ignores_other_users(client):
    from .test_isolation import new_client

    await register(client, "stats-mine@example.com")
    project_id = await create_project(client)
    await create_task(client, project_id, "Моя")

    async with new_client() as other:
        await register(other, "stats-other@example.com")
        response = await other.get("/api/v1/analytics/summary")

    assert response.json()["total"] == 0


# --- Уведомления ---------------------------------------------------------


async def test_notification_settings_round_trip(client):
    await register(client, "notify@example.com")

    defaults = await client.get("/api/v1/notifications/settings")
    updated = await client.put(
        "/api/v1/notifications/settings",
        json={
            "email_enabled": True,
            "telegram_enabled": False,
            "lead_time_minutes": 120,
        },
    )

    assert defaults.json()["email_enabled"] is True
    assert updated.status_code == 200
    assert updated.json()["lead_time_minutes"] == 120


async def test_chat_id_cannot_be_set_through_settings(client):
    """Чат — не поле формы: иначе в настройки записывался бы чужой чат."""
    await register(client, "notg@example.com")

    response = await client.put(
        "/api/v1/notifications/settings",
        json={
            "email_enabled": True,
            "telegram_enabled": False,
            "telegram_chat_id": "123456789",
            "lead_time_minutes": 60,
        },
    )

    assert response.status_code == 422


async def test_telegram_cannot_be_enabled_without_a_confirmed_chat(client):
    await register(client, "notg2@example.com")

    response = await client.put(
        "/api/v1/notifications/settings",
        json={"email_enabled": True, "telegram_enabled": True, "lead_time_minutes": 60},
    )

    assert response.status_code == 422


def _last_telegram_code() -> str:
    """Код из сообщения, ушедшего в заглушку Telegram."""
    from app.main import app

    text = app.state.telegram.outbox[-1].text
    return next(part for part in text.split() if part.isdigit())


async def test_telegram_chat_is_confirmed_by_a_code_sent_into_it(client):
    """Введённое число — не доказательство владения чатом; код в чате — да."""
    await register(client, "tg-link@example.com")
    chat_id = "123456789"

    issued = await client.post(
        "/api/v1/notifications/telegram/link",
        json={"chat_id": chat_id},
    )
    assert issued.status_code == 202, issued.text

    # До подтверждения чат в настройках не появляется.
    before = await client.get("/api/v1/notifications/settings")
    assert before.json()["telegram_chat_id"] is None

    wrong = await client.post(
        "/api/v1/notifications/telegram/confirm",
        json={"code": "000000"},
    )
    assert wrong.status_code == 403

    confirmed = await client.post(
        "/api/v1/notifications/telegram/confirm",
        json={"code": _last_telegram_code()},
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["telegram_chat_id"] == chat_id
    assert confirmed.json()["telegram_enabled"] is True


async def test_confirmation_code_is_single_use(client):
    await register(client, "tg-replay@example.com")
    await client.post(
        "/api/v1/notifications/telegram/link",
        json={"chat_id": "987654321"},
    )
    code = _last_telegram_code()

    first = await client.post(
        "/api/v1/notifications/telegram/confirm",
        json={"code": code},
    )
    await client.delete("/api/v1/notifications/telegram")
    replay = await client.post(
        "/api/v1/notifications/telegram/confirm",
        json={"code": code},
    )

    assert first.status_code == 200
    assert replay.status_code == 403


async def test_unlinking_the_chat_disables_delivery(client):
    await register(client, "tg-unlink@example.com")
    await client.post(
        "/api/v1/notifications/telegram/link",
        json={"chat_id": "555000111"},
    )
    await client.post(
        "/api/v1/notifications/telegram/confirm",
        json={"code": _last_telegram_code()},
    )

    unlinked = await client.delete("/api/v1/notifications/telegram")

    assert unlinked.status_code == 200
    assert unlinked.json()["telegram_chat_id"] is None
    assert unlinked.json()["telegram_enabled"] is False


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


async def test_worker_retries_after_failed_delivery(client, monkeypatch):
    """Заявка снимается, если ни один канал не сработал.

    Отметка ставится до отправки — иначе два воркера пошлют одно и то же.
    Оставленная после сбоя, она молча проглотила бы напоминание навсегда.
    """
    from app import worker
    from app.integrations.mail import Mailer

    await register(client, "retry@example.com")
    project_id = await create_project(client)
    await create_task(
        client,
        project_id,
        "Просрочена",
        due_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
    )

    class BrokenMailer(Mailer):
        async def send(self, to: str, subject: str, body: str) -> None:
            raise ConnectionError("SMTP недоступен")

    monkeypatch.setattr(worker, "create_mailer", lambda settings: BrokenMailer())
    assert await worker.run_once() == 0
    async with SessionLocal() as session:
        assert (await session.scalars(select(TaskNotification.kind))).all() == []

    monkeypatch.undo()
    assert await worker.run_once() == 1
