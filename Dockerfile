# Бэкенд не помещается в serverless: SSE держит соединение минутами, а
# подписчик Redis Pub/Sub должен жить между запросами. Поэтому обычный
# контейнер (Fly, Railway, VPS), а на Vercel — только SPA.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Слой зависимостей отдельно от кода: правка приложения не пересобирает его.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN uv sync --frozen --no-dev

# Процесс не должен работать от root.
RUN useradd --create-home --uid 10001 todo \
    && mkdir -p /app/var/uploads \
    && chown -R todo:todo /app
USER todo

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# Веб-процесс. Воркер напоминаний запускается отдельной командой:
#   docker run ... python -m app.worker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
