# The cloud server: one image for `api` (the website's server) and `reports` (the weekly report), with different commands.
# Built from the repo's top folder, so uv sees the whole workspace and uv.lock; compose.yaml does that:
#   cd cloud && docker compose up -d --build
# Same package versions as on the computer: uv installs them from uv.lock.
FROM python:3.12.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /src
# The packages first, so a change to the server's code doesn't install them all again.
COPY pyproject.toml uv.lock .python-version ./
COPY shared/pyproject.toml shared/
COPY cloud/server/pyproject.toml cloud/server/
RUN uv sync --frozen --package desktalk-cloud --no-dev --no-install-workspace

# Then the code: the server and desktalk_shared, installed into /opt/venv.
COPY shared/ shared/
COPY cloud/server/ cloud/server/
RUN uv sync --frozen --package desktalk-cloud --no-dev --no-editable

# The programs read settings.yaml and prompts.yaml from the folder they start in.
COPY cloud/settings.yaml cloud/prompts.yaml /config/

# It runs as a user without root rights.
RUN useradd --create-home cloud
USER cloud
WORKDIR /config

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "desktalk_cloud.main"]
