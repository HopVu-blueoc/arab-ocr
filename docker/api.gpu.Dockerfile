# Backend image, GPU (CUDA). UNVERIFIED - built for the target Ubuntu+CUDA
# box this project has always deferred to (see README's "Later: Ubuntu +
# CUDA" section); there is no GPU available to test this Dockerfile against
# where it was written. Sanity-check it on the real box before relying on it:
#   docker compose -f docker-compose.yml -f docker-compose.gpu.yml build api
#   docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm api \
#     python -c "import paddle; paddle.utils.run_check()"
#
# CUDA 12.6 chosen because it's the version paddlepaddle-gpu==3.3.1 publishes
# a wheel for (paddlepaddle.org.cn/packages/stable/cu126/) - confirmed against
# PaddlePaddle's own install docs, not guessed. Uses the official NVIDIA CUDA
# base image rather than PaddlePaddle's own prebuilt image (hosted on a Baidu
# registry) so this doesn't depend on reaching a registry an on-prem
# deployment may not have network access to.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04

# libgomp1 confirmed required by running the CPU image (docker/api.Dockerfile)
# for real - paddle's compiled core needs it or the worker crashes on startup.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# The CUDA base image ships Ubuntu 22.04's system Python (3.10), not the 3.12
# this project is pinned to - let uv fetch and manage its own interpreter
# rather than fighting the distro's Python.
RUN uv python install 3.12

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --no-cache

COPY app ./app
RUN uv sync --frozen --no-dev --no-cache

# Swap the CPU paddlepaddle the lockfile resolved for the GPU build. Done as
# a plain `uv pip install` (not `uv sync`, which would just reinstall the CPU
# wheel to match the lockfile) - this is the one dependency this image
# deliberately diverges from uv.lock for.
RUN uv pip install "paddlepaddle-gpu==3.3.1" \
    -i https://www.paddlepaddle.org.cn/packages/stable/cu126/

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
