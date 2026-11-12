FROM ubuntu:noble-20260810@sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316

LABEL workpiece.orcaslicer.version="2.5.0" \
      workpiece.orcaslicer.asset.sha256="d13eb0a9cd618ed080ab6b82c64c0a60c4501aac91e88d5ac2c3b526fb141a7c"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ORCA_VERSION=2.5.0 \
    ORCA_BIN=/opt/orca/squashfs-root/AppRun \
    PROFILE_ROOT=/app/profiles \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl python3 python3-venv xvfb \
      libdbus-1-3 libegl1 libgl1 libglu1-mesa libopengl0 libglib2.0-0t64 libgtk-3-0t64 \
      libwebkit2gtk-4.1-0 libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
      libx11-6 libxext6 libxi6 libxkbcommon0 libxkbcommon-x11-0 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/orca \
    && curl --fail --location --retry 3 \
      "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.5.0/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0.AppImage" \
      -o /tmp/orca.AppImage \
    && echo "d13eb0a9cd618ed080ab6b82c64c0a60c4501aac91e88d5ac2c3b526fb141a7c  /tmp/orca.AppImage" | sha256sum -c - \
    && chmod +x /tmp/orca.AppImage \
    && cd /opt/orca \
    && /tmp/orca.AppImage --appimage-extract \
    && rm /tmp/orca.AppImage

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY profiles ./profiles

RUN useradd --create-home --uid 10001 slicer \
    && chown -R slicer:slicer /app /opt/orca
USER slicer

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
