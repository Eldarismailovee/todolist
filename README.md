# Todo App

Реализация по ТЗ «Высоконагруженный Todo App с динамической
JSONB-архитектурой»: FastAPI (async), SQLAlchemy 2.0, PostgreSQL 16 с
GIN-индексом по JSONB, Redis Pub/Sub, SSE через `sse-starlette`, одноразовые
opaque-токены с ротацией refresh — и SPA на React 19 / Vite / TanStack Query
с Bento-сеткой и динамической формой по справочнику атрибутов.

## Запуск

```bash
docker compose up -d                 # PostgreSQL :55433, Redis :56379
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run python -m app.cli seed-attributes
uv run python -m app.cli create-admin admin@example.com 'длинный-пароль'
uv run fastapi dev app/main.py       # http://127.0.0.1:8000/api/docs
```

В соседнем терминале — SPA:

```bash
cd frontend
npm install
npm run dev                          # http://localhost:5173
```

Vite проксирует `/api` на бэкенд, поэтому SPA и API живут на одном origin:
иначе refresh cookie с `SameSite=Strict` и точная проверка `Origin` не
работают. `ALLOWED_ORIGIN` в `.env` должен совпадать с адресом Vite.

**Бэкенд должен быть запущен**: без него прокси отвечает `502` с текстом
«Бэкенд http://127.0.0.1:8000 недоступен…», и этот текст виден в интерфейсе.
Адрес переопределяется переменной `BACKEND_URL`.

Порты в `docker-compose.yml` нестандартные (55433/56379), потому что 5432 и
55432 на этой машине заняты другими проектами.

## Тесты

```bash
timeout 600 uv run pytest -q         # 70 тестов
uv run ruff check app tests
```

Тесты работают с настоящими PostgreSQL и Redis (база `todo_test`, префикс
ключей `todo:test`) и поднимают **два процесса uvicorn** на портах 8099/8100:
одноразовость токенов и Pub/Sub проверяются между разными воркерами, а не
внутри одного процесса.

Фронтенд:

```bash
cd frontend
npm run build                        # tsc --noEmit + vite build
node e2e/smoke.mjs                   # 15 проверок в настоящем Chromium
```

Смоук требует запущенных бэкенда (:8000) и Vite (:5173) и использует системный
Chromium (`CHROMIUM_PATH`), поэтому браузеры Playwright не скачиваются. Он
проверяет то, чего не видят `tsc` и сборка: вход, отсутствие токенов в
localStorage/sessionStorage, построение формы по справочнику, доставку события
по SSE во вторую вкладку, переключение темы и выход.

## Архитектурные решения

**Авторизация.** Access token — 256 бит случайности, хранится в Redis по
SHA-256 и гасится через `GETDEL`: два одновременных запроса с одним значением
не могут авторизоваться оба. Refresh — в `HttpOnly; Secure; SameSite=Strict`
cookie с `Path=/api/v1/auth/`; ротация идёт одной транзакцией с
`SELECT ... FOR UPDATE` по записи токена и сессии. Повторное предъявление
известного использованного значения отзывает семейство сессии, и отзыв
фиксируется до возврата 401. Неизвестное значение не отзывает ничего.

Порядок в `auth_service._finalize`: `flush` → выдача access в Redis → `commit`.
Сбой Redis откатывает ротацию, старый refresh остаётся годным, клиент
безопасно повторяет обмен. Сбой commit снимает уже выданный access.

**Авторитет состояния сессии — PostgreSQL.** `session_is_active()` открывает
собственную короткую сессию БД, поэтому SSE не удерживает SQL-соединение весь
срок потока. В потоке отзыв проверяется по таймеру
(`SSE_REVOCATION_CHECK_SECONDS`, по умолчанию 5 с) и дополнительно перед
отправкой каждого события.

**SSE.** `EventSourceResponse` сам следит за разрывом соединения и владеет
ASGI-каналом `receive`, поэтому в генераторе нет своего опроса
`request.is_disconnected()` — второй потребитель того же канала мог бы
перехватить сообщение о разрыве. Поток закрывается по `deadline =
min(expires_at - 15, now + 240)`; переподключение требует нового токена.
Подписки берут отдельный пул Redis: каждая занимает соединение целиком.

**Валидация атрибутов.** Схема строится по `TaskAttributeMeta` через
`create_model` со строгими типами и `extra="forbid"`, кэш — `lru_cache` по
содержимому метаданных. `"false"` и `0` не проходят как boolean, дата — только
существующая календарная `YYYY-MM-DD`, `null` необязательного поля
нормализуется в отсутствие ключа, обязательный boolean может быть `false`.

**Лимиты тела.** `MaxBodySizeMiddleware` считает фактически прочитанные байты,
а не только `Content-Length`.

**Идемпотентность.** `POST /tasks` принимает `Idempotency-Key`: запись в Redis
привязана к владельцу и хешу тела, тот же ключ с другим телом даёт 409,
неудачная попытка освобождает ключ.

**Фронтенд.** Access token живёт только в памяти вызова: `api.interceptors`
берёт новый токен перед каждым защищённым запросом, и каждый вызов
`freshAccess` делает свой обмен refresh — общий Promise на несколько запросов
нарушил бы одноразовость. Все операции с cookie идут под одним Web Lock
`todo-auth-cookie`, поэтому вкладки не обменивают refresh параллельно; без
Web Locks API клиент требует повторного входа, а не работает небезопасно.
Выход рассылается остальным вкладкам через BroadcastChannel.

Типы берутся из OpenAPI (`npm run gen:api` → `src/api/schema.d.ts`), а не
пишутся руками: расхождение контракта ломает сборку, а не рантайм.
Порядок полей формы и карточки задаёт `lib/fields.ts` — справочник приходит
отсортированным по коду, и обязательное «Название» иначе оказалось бы после
необязательной «Заметки».

## Проверенные критерии приёмки

Каждый пункт таблицы раздела 6 ТЗ закрыт тестом:

| Проверка | Тест |
| --- | --- |
| Событие получает только владелец | `test_sse.py::test_stream_delivers_owner_events_only` |
| Чужой `project_id` → 404, событие не публикуется | `test_isolation.py::test_foreign_project_id_gives_404_and_publishes_nothing` |
| Токен только в query string → 401 | `test_auth_tokens.py::test_token_in_query_string_is_not_authorization` |
| Один access одновременно дважды, включая разные воркеры | `test_auth_tokens.py::test_concurrent_use_of_one_access_token_lets_only_one_through`, `test_cross_worker.py::test_one_access_token_is_burned_across_workers` |
| Повтор SSE с использованным access | `test_sse.py::test_sse_token_cannot_be_replayed` |
| Purpose `sse` на REST и наоборот | `test_auth_tokens.py::test_purpose_mismatch_rejected_and_token_burned` |
| Токен старше 300 секунд | `test_auth_tokens.py::test_expired_access_rejected_regardless_of_key_ttl`, `test_expired_refresh_rejected` |
| Повтор использованного refresh отзывает семейство | `test_auth_tokens.py::test_refresh_reuse_revokes_the_session_family` |
| Параллельные обмены refresh | `test_auth_tokens.py::test_concurrent_refresh_with_same_cookie_serializes` |
| Несуществующая дата, timestamp, строка вместо boolean, неизвестное поле | `test_attributes.py::test_invalid_attributes_rejected` (11 случаев) |
| Обязательный boolean равен `false` | `test_attributes.py::test_required_boolean_false_is_accepted` |
| Экспорт без чужих данных, без N+1 и секретов | `test_export.py` |
| Событие с другого воркера доходит в поток | `test_cross_worker.py::test_event_published_by_one_worker_reaches_stream_on_another` |
| Logout при открытом SSE | `test_sse.py::test_logout_ends_open_stream` |
| Сбой публикации после commit | `test_limits.py::test_publish_failure_does_not_mask_successful_write` |

## Что не сделано

- **Фоновая сверка** сделана простым `refetchInterval` (60 с) и refetch при
  возврате фокуса; отдельной логики сверки версий нет.
- **Экран администратора** для справочника атрибутов: правка через API
  (`/api/v1/admin/task-attributes`) и CLI, UI для неё не сделан.
- **Оптимистичные обновления** не используются: после мутации выполняется
  инвалидация, состояние приходит с сервера.
- **Фоновый экспорт.** Синхронный экспорт ограничен `EXPORT_MAX_TASKS` (5 000);
  выше порога возвращается 413. Порционная выгрузка со согласованным снимком и
  защищённой выдачей файла не реализована.
- **Transactional outbox.** Публикация после commit — «по возможности»:
  при падении процесса между commit и publish событие теряется, клиент узнает
  об изменении при следующем reconnect (`ready` → полная инвалидация) или
  фоновой сверке.
- **WebAuthn / Passkeys** — только заготовка в виде разделения auth-слоя.
- **Удаление аккаунта** удаляет строки в основной БД; обработка резервных копий
  и журналов требует отдельной политики хранения.
- **Регуляторные заявления.** Реализованы инженерные механизмы из раздела 4;
  утверждения о соответствии нормам ЕС не проверялись.

## Замечание к ТЗ

Строгая одноразовость access-токена добавляет один запрос `POST /auth/refresh`
на каждый защищённый запрос и сериализует обмен cookie между вкладками. Это
удваивает число round-trip'ов и делает частоту обращений к auth-хранилищу равной
общей частоте запросов; нагрузочный профиль нужно считать с этим множителем.
Требование реализовано как описано в ТЗ.
