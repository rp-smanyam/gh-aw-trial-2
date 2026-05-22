FROM python:3.13-slim-bookworm AS base

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.4.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY tests/stubbed_mcp.py /app
COPY tests/date_helpers.py /app/tests/date_helpers.py
RUN --mount=type=cache,target=/root/.cache/uv uv init
RUN --mount=type=cache,target=/root/.cache/uv uv add fastmcp python-dateutil
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

FROM base
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Create non-root user and change ownership
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8042

CMD ["python", "stubbed_mcp.py"]
