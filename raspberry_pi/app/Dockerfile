# The app (the desktalk code), for the Raspberry Pi: no PyTorch or YOLO, since the detector container does the finding.
# Built from the repo's top folder; raspberry_pi/compose.yaml does that:
#   cd raspberry_pi && docker compose up -d --build
# Same package versions as on the computer: uv installs them from uv.lock.
# The image holds the code and config only. No API keys: Dockerfile.dockerignore keeps .env and data out of the build.
FROM python:3.12.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv

# OpenCV needs the first two. tzdata lets TZ in raspberry_pi/.env work: without it the app's clock stays on UTC.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /desk-talk
# The packages first, so a change to the app's code doesn't install them all again.
COPY pyproject.toml uv.lock .python-version README.md ./
COPY shared/pyproject.toml shared/
COPY raspberry_pi/detector_service/pyproject.toml raspberry_pi/detector_service/
COPY cloud/server/pyproject.toml cloud/server/
RUN uv sync --frozen --package desktalk --no-dev --no-install-workspace

# Then the code. It stays in this folder (an editable install) because the app finds config/ and its pages next to it.
COPY shared/ shared/
COPY desktalk/ desktalk/
COPY config/ config/
COPY web_app/ web_app/
COPY phone_camera/ phone_camera/
COPY server.py ./
COPY scripts/link_cloud.py scripts/
RUN uv sync --frozen --package desktalk --no-dev

COPY raspberry_pi/app/start.sh /usr/local/bin/desktalk-start
RUN chmod 755 /usr/local/bin/desktalk-start

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["desktalk-start"]
CMD ["python", "server.py"]
