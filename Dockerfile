# The HR interface is built here and nowhere else: this stage has Node, and
# the runtime image below does not. Only the compiled files cross over
# (SPEC §14).
FROM node:22-alpine AS ui

WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build


FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

WORKDIR /srv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY tools ./tools
RUN uv sync --frozen --no-dev

# The built interface. No Node, no sources, no build tools in this image.
COPY --from=ui /ui/dist /srv/ui

ENV PATH="/srv/.venv/bin:$PATH"

# Two ports from one process: the receiver for the device, the HR interface for
# people. Plain HTTP — the device cannot do anything else, and neither listener
# leaves the LAN except the HR one through Tailscale (SPEC §12, §14).
CMD ["python", "-m", "app.serve"]
