FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

WORKDIR /srv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY tools ./tools
RUN uv sync --frozen --no-dev

ENV PATH="/srv/.venv/bin:$PATH"

# Plain HTTP. The device cannot do anything else, and this listener never
# leaves the LAN (SPEC.md §12, §14).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
