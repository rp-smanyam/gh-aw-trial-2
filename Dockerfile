FROM python:3.13.1-slim-bookworm

ENV PYTHONUNBUFFERED=True
WORKDIR /workspace

# Install git + SSH client (needed to clone private repos)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git openssh-client && \
    rm -rf /var/lib/apt/lists/*

# Trust GitHub's SSH host key so git clone doesn't hang waiting for confirmation
RUN mkdir -p -m 0700 ~/.ssh && \
    ssh-keyscan -H github.com >> ~/.ssh/known_hosts

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

COPY pyproject.toml uv.lock /workspace/
RUN --mount=type=ssh uv sync --frozen --no-install-workspace --no-cache --package=agent-leasing

COPY . /workspace
RUN --mount=type=ssh uv sync --frozen --no-cache --package=agent-leasing

# Create non-root user and change ownership
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /workspace

USER appuser

CMD ["/workspace/.venv/bin/python", "src/agent_leasing/server.py"]
