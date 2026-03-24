FROM python:3.11-slim AS base

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 
    UV_LINK_MODE=copy

COPY pyproject.toml ./
RUN uv sync --locked --all-extras

COPY . .

ENTRYPOINT ["uv", "run", "pytest", "tests"]
