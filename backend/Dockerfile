FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./

FROM base AS test

COPY app ./app
COPY tests ./tests
RUN pip install --no-cache-dir ".[dev]"
RUN ruff check . && mypy && pytest

FROM base AS runtime

COPY app ./app
RUN pip install --no-cache-dir "."

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
