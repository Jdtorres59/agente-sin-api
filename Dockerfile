FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TRACE_DIR=/data/runs \
    MEMORY_DB=/data/memory.db

COPY --from=ghcr.io/astral-sh/uv:0.10.3 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY agent ./agent
COPY service ./service

RUN mkdir -p /data/runs
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync uvicorn service.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
