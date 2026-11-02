# The camera program, for the Raspberry Pi. Built from the repo's top folder:
#   docker build -f raspberry_pi/camera/Dockerfile -t desktalk-camera:local .
# Everything comes from apt, as on the Pi itself: Picamera2 and libcamera from Raspberry Pi's own apt source,
# because Debian's own libcamera doesn't know the Pi's camera chips.
# Bookworm's packages work with a newer Pi (trixie) underneath. Its Python is 3.11, the oldest desktalk_shared allows.
# To run it, it needs every /dev/video*, /dev/media*, /dev/v4l-subdev*, /dev/dma_heap, and /run/udev read-only.
# Without root it also needs the video group. docs/plans/2026-10-30-pi-first-run-notes.md has the exact commands.
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
         -o /usr/share/keyrings/raspberrypi-archive-keyring.asc \
    && echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.asc] http://archive.raspberrypi.com/debian/ bookworm main" \
         > /etc/apt/sources.list.d/raspi.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3-picamera2 python3-opencv python3-yaml \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Only what the camera program needs. It finds desktalk_shared and config/settings.yaml next to itself.
WORKDIR /desk-talk
COPY shared/desktalk_shared/ shared/desktalk_shared/
COPY raspberry_pi/camera/ raspberry_pi/camera/
COPY config/settings.yaml config/settings.yaml

ENV PYTHONUNBUFFERED=1

CMD ["python3", "-m", "raspberry_pi.camera"]
