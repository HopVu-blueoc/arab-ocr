"""Every Settings field must actually reach a container, or .env is a lie.

docker/nginx.conf's client_max_body_size is checked separately in
tests/test_limits.py; this file checks docker-compose.yml's environment map
against app/config.py's Settings fields.
"""

import re
from pathlib import Path

from app.config import Settings, get_settings

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


def _api_service_env_keys() -> set[str]:
    """Env var names set on the `api` service's &backend-env anchor block."""
    text = COMPOSE.read_text(encoding="utf-8")
    block = text.split("environment: &backend-env", 1)[1]
    block = block.split("\n    volumes:", 1)[0]  # stop at the next sibling key
    return set(re.findall(r"^\s{6}([A-Z0-9_]+):", block, re.MULTILINE))


def test_every_settings_field_is_passed_through_compose():
    env_keys = _api_service_env_keys()
    missing = [
        name.upper()
        for name in Settings.model_fields
        if name.upper() not in env_keys
    ]
    assert missing == [], (
        f"these Settings fields are not in docker-compose.yml's backend-env, "
        f"so .env can never change them in Docker: {missing}"
    )


def test_a_blank_detection_knob_is_treated_as_unset(monkeypatch):
    """docker-compose.yml passes `${OCR_DET_THRESH:-}`, which is an empty
    string when the host never set it - not a missing env var. Settings must
    read that the same as never having set it at all."""
    monkeypatch.setenv("OCR_DET_THRESH", "")
    get_settings.cache_clear()
    try:
        assert get_settings().ocr_det_thresh is None
    finally:
        get_settings.cache_clear()


def test_a_real_detection_knob_value_still_parses(monkeypatch):
    monkeypatch.setenv("OCR_DET_THRESH", "0.35")
    get_settings.cache_clear()
    try:
        assert get_settings().ocr_det_thresh == 0.35
    finally:
        get_settings.cache_clear()
