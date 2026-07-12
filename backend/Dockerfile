FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./

FROM base AS test

COPY app ./app
COPY tests ./tests
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir ".[dev]"
RUN ruff check . && mypy && pytest

FROM base AS runtime

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir "."

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m app.commerce.seed --if-empty && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
