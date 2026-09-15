# Test image: the real backend image plus the dev dependency group and the
# test suite. Runs in Linux Docker so results match the deployment target
# rather than whatever a developer's host happens to have installed.
#
#   docker compose build api                     # produces arabic-ocr-api
#   docker build -f docker/test.Dockerfile -t arab-ocr-test .
#   docker run --rm arab-ocr-test
#
# The suite needs no Redis and no object store: tests/conftest.py pins
# JOB_BACKEND=inline, STORAGE_BACKEND=local and swaps in a fake OCR engine.
# The S3 storage tests skip unless S3_TEST_ENDPOINT points at a live bucket.
ARG BASE=arabic-ocr-api:latest
FROM ${BASE}

WORKDIR /app

# --only-group dev resolves from the same locked versions the app image used,
# so the test run can't silently drift onto different dependencies.
RUN uv export --frozen --only-group dev --no-hashes -o /tmp/dev-requirements.txt \
    && uv pip install -r /tmp/dev-requirements.txt \
    && rm /tmp/dev-requirements.txt

# pyproject.toml carries the pytest config (pythonpath, testpaths, markers).
COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
# Re-copied rather than inherited: the base image only supplies the installed
# dependency layer, and if it were built from older source the migration drift
# test would compare fresh migrations against stale models and fail for a
# reason that has nothing to do with the code under test.
COPY app ./app

ENV PYTHONPATH=/app

CMD ["python", "-m", "pytest", "-q"]
