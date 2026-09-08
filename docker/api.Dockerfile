# Backend image, CPU. Used for both the `api` and `worker` compose services -
# they share 100% of the codebase and dependencies, only the start command
# differs (see docker-compose.yml's `command:` override for worker).
FROM python:3.12-slim

# libgl1/libglib2.0-0: paddleocr pulls in opencv-contrib-python (not the
# -headless build), which links against libGL at import time - missing on a
# slim base, so paddleocr would fail with "libGL.so.1: cannot open shared
# object file" on first import without these. libgomp1: paddle's compiled
# core needs GNU OpenMP - without it the worker crashes on startup with
# "libgomp.so.1: cannot open shared object file" the moment it builds the
# OCR engine (confirmed against a real run of this image, not assumed).
# curl: used by this image's own healthcheck (docker-compose.yml).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependencies first so this layer only rebuilds when pyproject.toml/uv.lock
# change, not on every source edit - paddlepaddle alone is 100+MB.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --no-cache

COPY app ./app
RUN uv sync --frozen --no-dev --no-cache

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
