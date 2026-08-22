FROM ubuntu:24.04

ARG ORCA_VERSION=2.4.2
ARG ORCA_ASSET=OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ORCA_VERSION=${ORCA_VERSION} \
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
      "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v${ORCA_VERSION}/${ORCA_ASSET}" \
      -o /tmp/orca.AppImage \
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
