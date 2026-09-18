# PaddleOCR-VL vLLM Handoff

Date: 2026-09-18  
Branch: `codex/paddle-vl-vllm`  
Base branch: `feat/scale-readiness`  
Implementation commit: `e6750dd`

## Goal

Keep classic PaddleOCR as the supported default while adding PaddleOCR-VL as
an optional Docker Compose deployment for an RTX 5060 Ti (Blackwell, sm_120,
16 GiB VRAM). All Python execution and tests must run in Linux containers;
the project has not been validated as a native Windows Python application.

## Architecture

```text
browser -> FastAPI -> Redis/Celery worker -> internal vLLM service -> GPU 0
```

FastAPI remains the public application API. The Celery worker remains a CPU
client in the VL deployment. Only Paddle's official Blackwell vLLM container
loads the 0.9B model and owns the GPU. The VLM service has no published host
port.

The VL integration deliberately uses `use_layout_detection=False` with
`prompt_label="spotting"`. This preserves the existing application contract:
one text string and quadrilateral per detected line. PaddleOCR-VL supplies no
per-line confidence, so the adapter continues to store a synthetic `1.0` and
the result must be reviewed manually.

## Work-item status

| Work item | Status | Evidence / notes |
| --- | --- | --- |
| Diagnose Arabic failure | Complete | PaddleOCR-VL 1.6 officially supports Arabic. The previous run stalled before generation; this was not a language-support failure. Native `predict()` matched PaddleOCR issue #17693: `auto_growth`, 0% GPU, futex wait. |
| Explain low GPU utilization | Complete | The stalled one-off native container retained about 7.9 GiB VRAM while sleeping. It was already absent when implementation resumed. |
| Fix Compose image-export collision | Complete | `migrate`, CPU `worker`, and `flower` reuse the image built by `api`; they no longer export the same tag concurrently. |
| Make worker concurrency configurable | Complete | Base worker uses `CELERY_WORKER_CONCURRENCY`; VL overlay forces concurrency 1. |
| Clean standard GPU image | Implemented, rebuild pending | GPU Dockerfile uninstalls CPU `paddlepaddle`, installs only `paddlepaddle-gpu==3.3.1` from the CUDA 12.9 index, and asserts that CPU Paddle is absent. The rebuilt GPU image has not yet been tested because of network cost. |
| Add PaddleX service client | Complete | `paddlex[ocr,genai-client]==3.7.2` is locked. CPU image built successfully; offline check returned `genai_client=True`, and a server-backed `PaddleOCRVL` object constructed with Docker networking disabled. |
| Add optional VL Compose overlay | Complete | `docker-compose.paddle-vl.yml` uses Paddle's official `latest-nvidia-gpu-sm120-offline` vLLM image, GPU 0, `shm_size: 64g`, health-gated worker startup, and no host port. |
| Add conservative vLLM tuning | Complete | `docker/paddle-vl-vllm.yml`: `gpu-memory-utilization: 0.7`, `max-num-seqs: 1`. It is bind-mounted, so tuning requires a restart but no rebuild or pull. |
| Add VL client configuration | Complete | Server URL, max concurrency, and request timeout are exposed through `Settings` and Compose. A server backend without a URL fails clearly during warmup. |
| Bound stalled VL requests | Complete | Linux `SIGALRM` bounds the PaddleX call at 600 seconds by default; the service layer records the resulting exception as an image failure. |
| Preserve standard deployment | Complete | `.env` is locally restored to classic `paddle`; the VL overlay alone overrides the worker to `paddle_vl`. The normal GPU command remains unchanged. |
| Pull official vLLM image | In progress / user-owned | Pull was stopped at the user's request. All completed layers are cached; the final 3.958 GB layer had reached about 1.126 GB. |
| Run real Arabic fixture through vLLM | Pending | Must wait for the official image pull and healthy server. Do not claim Paddle-VL accuracy is verified until this passes. |
| End-to-end UI upload test | Pending | After fixture smoke passes, upload the fixture and verify `queued -> running -> done`, Arabic rendering, and selectable polygons. |
| Failure-path test | Pending | Stop the VLM service during a request and confirm the image becomes `failed` within the configured timeout rather than remaining `running`. |
| Push branch | Blocked by credentials | Origin rejected authenticated user `andoan-blueoc` with HTTP 403 for `HopVu-blueoc/arab-ocr`. Local commits and branch are intact. |

## Validation already completed

- Base, standard GPU, and Paddle-VL merged Compose configurations all passed
  `docker compose ... config --quiet`.
- CPU application image built successfully with the locked genai-client extra.
- PaddleX genai-client availability check passed inside the built image.
- Server-backed `PaddleOCRVL` construction passed with `--network none`, proving
  the client does not download/load the 0.9B model locally.
- Full Docker backend suite: `161 passed, 6 skipped, 1 deselected`.
- Focused VL/config tests: `11 passed`.
- Focused Ruff check passed. Repository-wide Ruff reports pre-existing
  executable-bit/shebang warnings from the Windows checkout; no new lint issue
  remains in the touched Python files.

## Resume commands

Run from the repository root in PowerShell.

1. Resume the one-time official image pull:

   ```powershell
   docker compose -f docker-compose.yml -f docker-compose.paddle-vl.yml pull paddleocr-vlm-server
   ```

2. Start the optional VL deployment:

   ```powershell
   docker compose -f docker-compose.yml -f docker-compose.paddle-vl.yml up -d --build
   ```

3. Wait for the VLM service and worker:

   ```powershell
   docker compose -f docker-compose.yml -f docker-compose.paddle-vl.yml ps
   docker compose -f docker-compose.yml -f docker-compose.paddle-vl.yml logs -f paddleocr-vlm-server worker
   ```

4. Run the real Arabic fixture. The bind mount is required because production
   images intentionally do not contain `scripts/` or `tests/`:

   ```powershell
   docker compose -f docker-compose.yml -f docker-compose.paddle-vl.yml run --rm --no-deps `
     --volume "${PWD}:/workspace" --workdir /workspace `
     worker python -u scripts/smoke_ocr_vl.py tests/fixtures/arabic_sample.png
   ```

   Expected fixture phrases include:

   - `مرحبا بالعالم`
   - `السطر الثاني`
   - `اختبار التعرف الضوئي`

5. Inspect GPU behavior during inference:

   ```powershell
   nvidia-smi -l 1
   ```

6. After credentials are corrected, push the existing branch:

   ```powershell
   git push -u origin codex/paddle-vl-vllm
   ```

## Important limitations

- Do not combine `docker-compose.gpu.yml` and
  `docker-compose.paddle-vl.yml`. The former runs classic PaddleOCR directly on
  the GPU worker; the latter reserves the GPU for vLLM and keeps the worker on
  the CPU application image.
- Manual region recognition is unsupported by PaddleOCR-VL and returns `501`.
- There is no silent fallback to classic PaddleOCR. Mixing engines inside one
  batch would make review results ambiguous, so VL failures remain explicit.
- Native PaddleOCR-VL is diagnostic only. If it is revisited, set
  `FLAGS_allocator_strategy=naive_best_fit` before importing Paddle and use a
  strict external timeout.
- FastDeploy is not selected because its published GPU support range stops
  below compute capability 12.0; this host is Blackwell sm_120.

## Primary references

- PaddleOCR-VL usage:
  <https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md>
- Blackwell deployment:
  <https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-NVIDIA-Blackwell.en.md>
- Official Blackwell Compose:
  <https://github.com/PaddlePaddle/PaddleOCR/blob/main/deploy/paddleocr_vl_docker/accelerators/nvidia-gpu-sm120/compose.yaml>
- Matching native allocator hang:
  <https://github.com/PaddlePaddle/PaddleOCR/issues/17693>
