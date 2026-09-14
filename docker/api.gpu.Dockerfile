# Backend image, GPU (CUDA). Verified working end-to-end on an RTX 5070
# (Blackwell, sm_120) - detection + recognition both ran on GPU and matched
# the CPU image's output on the same input.
#
# CUDA 12.9 required for Blackwell (RTX 50-series / sm_120) GPUs - the cu126
# wheel has no sm_120 kernels and silently computes zeros on those GPUs
# instead of erroring (confirmed: even `x + x` returns 0 on an RTX 5070 with
# the cu126 build). See PaddleOCR's own Blackwell guide, which pins cu129:
# https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-NVIDIA-Blackwell.en.md
# Uses the official NVIDIA CUDA base image rather than PaddlePaddle's own
# prebuilt image (hosted on a Baidu registry) so this doesn't depend on
# reaching a registry an on-prem deployment may not have network access to.
FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu22.04

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
    -i https://www.paddlepaddle.org.cn/packages/stable/cu129/

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
