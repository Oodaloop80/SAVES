FROM python:3.11-slim

# Identity (Bora, 2026-08-06). The container runs as a NON-ROOT service account whose
# UID/GID match a real DSM account (sa_saves), so every file SAVES writes into the vault,
# the media store, and the state dir is owned by that account rather than by root. Root in
# a container writing to a bind-mounted vault produces root:root notes that the human and
# the Obsidian Sync client cannot edit or delete. Same pattern as the Forgejo stack on this
# NAS — see docs/FORGEJO.md §1 and docs/PROD_ROLLOUT.md §1.6.
#
# These MUST match `id sa_saves` on the target host or the bind mounts will not be
# writable. docker/.env supplies them to compose (SAVES_UID / SAVES_GID).
ARG SAVES_UID=1031
ARG SAVES_GID=65536

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    chromium \
    chromium-driver \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create the account BEFORE anything user-scoped is installed, so ownership is right from
# the start. `-o` allows a non-unique id: GID 65536 is DSM's shared "docker_service_accounts"
# group, deliberately reused across service containers.
RUN groupadd -o -g "${SAVES_GID}" saves \
    && useradd -o -u "${SAVES_UID}" -g "${SAVES_GID}" -m -d /home/saves -s /bin/bash saves

WORKDIR /app

COPY requirements.txt requirements-whisper.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-whisper.txt

# Playwright browsers go to a SHARED path, not $HOME/.cache. `playwright install` runs here
# as root and would otherwise drop Chromium in /root/.cache/ms-playwright, which the
# non-root runtime user cannot read — Playwright then fails at launch with "Executable
# doesn't exist". a+rX makes it readable and traversable by any UID.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN mkdir -p /opt/playwright \
    && playwright install chromium --with-deps \
    && chmod -R a+rX /opt/playwright

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config.yaml .
COPY .env.example .

# HOME must be set explicitly: instaloader resolves its session dir via
# os.path.expanduser("~/.config/instaloader") (src/extractors/instagram.py), which reads
# $HOME first. Pre-creating the two named-volume mountpoints owned by `saves` makes Docker
# seed each fresh volume with that ownership instead of root:root.
ENV HOME=/home/saves
RUN mkdir -p /home/saves/.config/instaloader /home/saves/.cache/whisper /app/logs \
    && chown -R "${SAVES_UID}:${SAVES_GID}" /home/saves /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

USER saves

CMD ["python", "-m", "src.main"]
