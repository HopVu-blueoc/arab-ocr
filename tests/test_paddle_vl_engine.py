import sys
import time
from types import ModuleType

import pytest

from app.config import Settings
from app.ocr.paddle_vl_engine import (
    PaddleOcrVLEngine,
    _request_timeout,
    parse_spotting_result,
)


def test_parses_polys_and_texts_into_ordered_lines():
    payload = {
        "res": {
            "spotting_res": {
                "rec_polys": [
                    # bottom line, listed first: reading order must fix this
                    [(60, 160), (440, 160), (440, 220), (60, 220)],
                    [(60, 40), (600, 40), (600, 100), (60, 100)],
                ],
                "rec_texts": ["الثاني", "مرحبا بالعالم"],
            }
        }
    }
    lines = parse_spotting_result(payload, width=1000, height=400)
    assert [ln.text for ln in lines] == ["مرحبا بالعالم", "الثاني"]
    assert all(ln.source == "paddleocr-vl" for ln in lines)
    assert all(ln.score == 1.0 for ln in lines)
    assert lines[0].polygon == [(60.0, 40.0), (600.0, 40.0), (600.0, 100.0), (60.0, 100.0)]


def test_accepts_unwrapped_payload_without_res_key():
    payload = {"spotting_res": {"rec_polys": [[(0, 0), (10, 0), (10, 5), (0, 5)]], "rec_texts": ["hi"]}}
    lines = parse_spotting_result(payload, width=10, height=5)
    assert [ln.text for ln in lines] == ["hi"]


def test_drops_punctuation_only_lines():
    payload = {
        "spotting_res": {
            "rec_polys": [
                [(0, 0), (10, 0), (10, 5), (0, 5)],
                [(0, 10), (10, 10), (10, 15), (0, 15)],
            ],
            "rec_texts": ["-", "السطر"],
        }
    }
    lines = parse_spotting_result(payload, width=10, height=15)
    assert [ln.text for ln in lines] == ["السطر"]


def test_missing_spotting_res_yields_no_lines():
    assert parse_spotting_result({"res": {}}, width=100, height=100) == []


def test_empty_texts_and_polys_yields_no_lines():
    payload = {"spotting_res": {"rec_polys": [], "rec_texts": []}}
    assert parse_spotting_result(payload, width=100, height=100) == []


def test_service_backend_passes_url_and_concurrency(monkeypatch):
    calls = []

    class FakePaddleOCRVL:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    fake_module = ModuleType("paddleocr")
    fake_module.PaddleOCRVL = FakePaddleOCRVL
    monkeypatch.setitem(sys.modules, "paddleocr", fake_module)

    engine = PaddleOcrVLEngine(
        Settings(
            ocr_vl_backend="vllm-server",
            ocr_vl_server_url="http://vlm:8118/v1",
            ocr_vl_max_concurrency=1,
            ocr_device="cpu",
        )
    )
    engine.warmup()

    assert calls == [
        {
            "vl_rec_model_name": "PaddleOCR-VL-1.6-0.9B",
            "vl_rec_backend": "vllm-server",
            "vl_rec_server_url": "http://vlm:8118/v1",
            "vl_rec_max_concurrency": 1,
            "use_layout_detection": False,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "device": "cpu",
        }
    ]


def test_service_backend_requires_url(monkeypatch):
    fake_module = ModuleType("paddleocr")
    fake_module.PaddleOCRVL = object
    monkeypatch.setitem(sys.modules, "paddleocr", fake_module)

    engine = PaddleOcrVLEngine(
        Settings(ocr_vl_backend="vllm-server", ocr_vl_server_url=None)
    )
    with pytest.raises(ValueError, match="OCR_VL_SERVER_URL is required"):
        engine.warmup()


def test_request_timeout_interrupts_stalled_service_call():
    with (
        pytest.raises(TimeoutError, match="PaddleOCR-VL request exceeded"),
        _request_timeout(0.01),
    ):
        time.sleep(1)
