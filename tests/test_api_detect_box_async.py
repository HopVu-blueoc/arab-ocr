"""detect_box under JOB_BACKEND=celery: dispatch + poll, instead of running
inference synchronously inside the API process (the P1 #7 defect - this
container has no GPU in the deployed topology, only `worker` does, and
nothing serialises concurrent request threads sharing one engine).

No real Celery/Redis here: `.delay()` and `AsyncResult` are monkeypatched to
keep the test fast and broker-free, exactly as tests/test_dispatch.py does
for the upload path.
"""

import pytest
from PIL import Image as PILImage

from app import dispatch
from app.config import get_settings
from app.ocr.crops import Candidate
from app.ocr.fake_engine import FakeOcrEngine

BOX = [[100.0, 100.0], [300.0, 100.0], [300.0, 150.0], [100.0, 150.0]]


@pytest.fixture
def image_id(client, upload, tmp_path):
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("b", [path])
    return client.get(f"/api/batches/{batch['id']}/images").json()["items"][0]["id"]


class _FakeAsyncResult:
    def __init__(self, job_id: str):
        self.id = job_id


def test_detect_box_dispatches_and_returns_202_with_a_job_id(
    client, image_id, monkeypatch
):
    monkeypatch.setattr(get_settings(), "job_backend", "celery")
    monkeypatch.setattr(
        dispatch, "_engine", FakeOcrEngine(recognize_quad_result=Candidate("يدوي", 0.93, "manual"))
    )

    from app.worker import tasks

    calls = []

    def fake_delay(image_id_arg, polygon_arg):
        calls.append((image_id_arg, polygon_arg))
        return _FakeAsyncResult("job-123")

    monkeypatch.setattr(tasks.detect_box_task, "delay", fake_delay)

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": BOX})

    assert resp.status_code == 202
    assert resp.json() == {"job_id": "job-123"}
    assert calls == [(image_id, BOX)]


def test_detect_box_still_validates_synchronously_under_celery(client, image_id, monkeypatch):
    """Box-size and image-existence checks never touch OCR - they must not
    round-trip through Celery at all, in either backend mode."""
    monkeypatch.setattr(get_settings(), "job_backend", "celery")

    from app.worker import tasks

    monkeypatch.setattr(
        tasks.detect_box_task,
        "delay",
        lambda *a: (_ for _ in ()).throw(AssertionError("should not dispatch")),
    )

    tiny = [[100.0, 100.0], [104.0, 100.0], [104.0, 104.0], [100.0, 104.0]]
    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": tiny})
    assert resp.status_code == 400

    resp = client.post("/api/images/9999/lines/detect-box", json={"polygon": BOX})
    assert resp.status_code == 404


def test_job_endpoint_reports_pending_before_the_task_finishes(client, monkeypatch):
    from app.routers import jobs

    class _Pending:
        def ready(self):
            return False

    monkeypatch.setattr(jobs, "AsyncResult", lambda *a, **k: _Pending())

    resp = client.get("/api/jobs/some-id")
    assert resp.status_code == 202
    assert resp.json() == {"status": "pending"}


def test_job_endpoint_returns_lines_on_success(client, monkeypatch):
    from app.routers import jobs

    class _Done:
        def ready(self):
            return True

        def failed(self):
            return False

        def __init__(self):
            self.result = {"ok": True, "status_code": 200, "lines": [{"id": 1, "rec_text": "x"}]}

    monkeypatch.setattr(jobs, "AsyncResult", lambda *a, **k: _Done())

    resp = client.get("/api/jobs/some-id")
    assert resp.status_code == 200
    assert resp.json() == [{"id": 1, "rec_text": "x"}]


def test_job_endpoint_maps_an_expected_no_match_to_422(client, monkeypatch):
    from app.routers import jobs

    class _NoMatch:
        def ready(self):
            return True

        def failed(self):
            return False

        def __init__(self):
            self.result = {
                "ok": False,
                "status_code": 422,
                "reason": "no text found in that region",
            }

    monkeypatch.setattr(jobs, "AsyncResult", lambda *a, **k: _NoMatch())

    resp = client.get("/api/jobs/some-id")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "no text found in that region"


def test_job_endpoint_maps_an_unhandled_task_exception_to_500(client, monkeypatch):
    from app.routers import jobs

    class _Crashed:
        def ready(self):
            return True

        def failed(self):
            return True

        result = RuntimeError("boom")

    monkeypatch.setattr(jobs, "AsyncResult", lambda *a, **k: _Crashed())

    resp = client.get("/api/jobs/some-id")
    assert resp.status_code == 500
