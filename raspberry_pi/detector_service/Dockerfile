# The detector service, for the Raspberry Pi (64-bit Raspberry Pi OS): FastAPI + Ultralytics, running ncnn models.
# Built from the repo's top folder, so uv sees the whole workspace and uv.lock; compose.yaml does that:
#   cd raspberry_pi/detector_service && docker compose up -d --build
# Same package versions as on the computer: uv installs them from uv.lock.
FROM python:3.12.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv

# OpenCV (used by Ultralytics) needs these two system libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /src
# The packages first (PyTorch, Ultralytics, ncnn, pnnx ...), so a change to the service's code
# doesn't install them all again.
COPY pyproject.toml uv.lock .python-version ./
COPY shared/pyproject.toml shared/
COPY raspberry_pi/detector_service/pyproject.toml raspberry_pi/detector_service/
RUN uv sync --frozen --package desktalk-detector --no-dev --no-install-workspace

# Then the code: the service and desktalk_shared, installed into /opt/venv.
COPY shared/ shared/
COPY raspberry_pi/detector_service/ raspberry_pi/detector_service/
RUN uv sync --frozen --package desktalk-detector --no-dev --no-editable

# It runs as a user without root rights. /models becomes a Docker volume (compose.yaml), so the
# models and their ncnn versions survive restarts and rebuilds.
RUN useradd --create-home detector && mkdir /models && chown detector /models
USER detector
WORKDIR /home/detector

# YOLO_AUTOINSTALL=false: a missing package fails with a clear message instead of being pip-installed while it runs.
# DETECTOR_HOST=0.0.0.0 is inside the container only: compose.yaml opens the port on the Pi's 127.0.0.1 alone.
# The other settings (port, picture size limit, waits) come from .env, through compose.yaml.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    YOLO_AUTOINSTALL=false \
    DETECTOR_HOST=0.0.0.0 \
    DETECTOR_MODELS_DIR=/models

CMD ["python", "-m", "desktalk_detector"]
