FROM python:3.12-slim AS lock

ARG PIP_TOOLS_VERSION=7.6.1
RUN python -m pip install --no-cache-dir "pip-tools==${PIP_TOOLS_VERSION}"

WORKDIR /workspace

ENTRYPOINT ["python", "-m", "piptools", "compile"]

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml requirements-py312-linux.lock ./

FROM base AS test

COPY app ./app
COPY tests ./tests
COPY alembic.ini ./
COPY migrations ./migrations
RUN python -m pip install --no-cache-dir \
    --constraint requirements-py312-linux.lock \
    ".[dev]" \
    && python -m pip check
RUN ruff check . && mypy && pytest

FROM base AS runtime

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN python -m pip install --no-cache-dir \
    --constraint requirements-py312-linux.lock \
    ".[embedding]" \
    && python -m pip check

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m app.commerce.seed --if-empty && python -m app.knowledge.reindex && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
