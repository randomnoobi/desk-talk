# The app (the desktalk code), for the Raspberry Pi: no PyTorch or YOLO, since the detector container does the finding.
# Built from the repo's top folder; raspberry_pi/compose.yaml does that:
#   cd raspberry_pi && docker compose up -d --build
# Same package versions as on the computer: uv installs them from uv.lock.
# The image holds the code and config only. No API keys: Dockerfile.dockerignore keeps .env and data out of the build.

# Piper, the program that speaks, and its voice: fetched once here, so the running app never downloads anything.
FROM debian:bookworm-slim AS voice
ARG PIPER_VERSION=2023.11.14-2
ARG PIPER_SHA256_x86_64=a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992
ARG PIPER_SHA256_aarch64=fea0fd2d87c54dbc7078d0f878289f404bd4d6eea6e7444a77835d1537ab88eb
ARG VOICE_COMMIT=b4e358ffcb232591bf73a540e70f7de528e4174a
ARG VOICE_SHA256=5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f
ARG VOICE_JSON_SHA256=efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && piper="$(uname -m)" \
    && case "$piper" in \
         aarch64) sha="$PIPER_SHA256_aarch64" ;; \
         x86_64) sha="$PIPER_SHA256_x86_64" ;; \
         *) echo "No Piper for $piper"; exit 1 ;; \
       esac \
    && mkdir /opt/piper && cd /opt/piper \
    && curl -fsSL -o piper.tgz "https://github.com/rhasspy/piper/releases/download/$PIPER_VERSION/piper_linux_$piper.tar.gz" \
    && echo "$sha  piper.tgz" | sha256sum -c - \
    && tar -xzf piper.tgz --strip-components=1 && rm piper.tgz \
    && voices="https://huggingface.co/rhasspy/piper-voices/resolve/$VOICE_COMMIT/en/en_US/lessac/medium" \
    && curl -fsSL -o en_US-lessac-medium.onnx "$voices/en_US-lessac-medium.onnx" \
    && curl -fsSL -o en_US-lessac-medium.onnx.json "$voices/en_US-lessac-medium.onnx.json" \
    && echo "$VOICE_SHA256  en_US-lessac-medium.onnx" | sha256sum -c - \
    && echo "$VOICE_JSON_SHA256  en_US-lessac-medium.onnx.json" | sha256sum -c -

FROM python:3.12.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv

# OpenCV needs the first two. tzdata lets TZ in raspberry_pi/.env work: without it the app's clock stays on UTC.
# alsa-utils is aplay, which plays what Piper says.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 tzdata alsa-utils \
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

COPY --from=voice /opt/piper /opt/piper
RUN ln -s /opt/piper/piper /usr/local/bin/piper

COPY raspberry_pi/app/start.sh /usr/local/bin/desktalk-start
COPY raspberry_pi/health_check.py /usr/local/bin/desktalk-health
RUN chmod 755 /usr/local/bin/desktalk-start

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["desktalk-start"]
CMD ["python", "server.py"]
