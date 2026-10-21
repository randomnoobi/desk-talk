# The YOLO benchmark, for the Raspberry Pi (64-bit Raspberry Pi OS).
# Same package versions as on the laptop: uv installs them from uv.lock.
# The code itself isn't copied in: compose.yaml mounts the project folder, so a
# `git pull` on the Pi is enough to get new code (only rebuild when uv.lock changes).
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

WORKDIR /opt/deskbench
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/repo/benchmarks \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /repo/benchmarks
ENTRYPOINT ["python", "-m", "deskbench.cli"]
