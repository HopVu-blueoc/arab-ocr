"""Upload limits have to agree across the proxy, the API and the client.

They are enforced in three different places, and disagreement shows up as a
413 with no per-file detail - the proxy refuses the body before the app sees
it, so nothing can explain to the user which file was the problem.
"""

import re
from pathlib import Path

import pytest

from app.config import get_settings

NGINX_CONF = Path(__file__).resolve().parent.parent / "docker" / "nginx.conf"

_SIZE = re.compile(r"client_max_body_size\s+(\d+)([kmg]?)\s*;", re.IGNORECASE)
_UNITS = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}


def _nginx_body_limit_bytes() -> int:
    match = _SIZE.search(NGINX_CONF.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError("no client_max_body_size in docker/nginx.conf")
    return int(match.group(1)) * _UNITS[match.group(2).lower()]


def test_nginx_accepts_a_full_size_request():
    """nginx caps the whole body, so it must clear the API's request budget."""
    settings = get_settings()
    assert _nginx_body_limit_bytes() >= settings.max_request_bytes, (
        "docker/nginx.conf's client_max_body_size is below MAX_REQUEST_MB - "
        "a request the API would accept gets a 413 at the proxy instead"
    )


def test_a_single_max_size_file_fits_in_one_request():
    settings = get_settings()
    assert settings.max_upload_bytes <= settings.max_request_bytes, (
        "a file at the per-file limit could never be uploaded"
    )


def test_limits_endpoint_reports_both_caps(client):
    body = client.get("/api/limits").json()
    settings = get_settings()
    assert body == {
        "max_file_bytes": settings.max_upload_bytes,
        "max_request_bytes": settings.max_request_bytes,
    }


@pytest.mark.parametrize("field", ["max_file_bytes", "max_request_bytes"])
def test_limits_are_positive(client, field):
    assert client.get("/api/limits").json()[field] > 0
