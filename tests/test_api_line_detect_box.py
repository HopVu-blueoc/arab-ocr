import pytest
from PIL import Image as PILImage

from app import dispatch
from app.ocr.crops import Candidate
from app.ocr.fake_engine import FakeOcrEngine

BOX = [[100.0, 100.0], [300.0, 100.0], [300.0, 150.0], [100.0, 150.0]]


@pytest.fixture
def image_id(client, upload, tmp_path):
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("b", [path])
    return client.get(f"/api/batches/{batch['id']}/images").json()["items"][0]["id"]


def test_detect_box_adds_a_new_line_with_the_engines_text(client, image_id, monkeypatch):
    monkeypatch.setattr(
        dispatch, "_engine", FakeOcrEngine(recognize_quad_result=Candidate("يدوي", 0.93, "manual"))
    )

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": BOX})

    assert resp.status_code == 200
    lines = resp.json()
    added = next(ln for ln in lines if ln["rec_text"] == "يدوي")
    assert added["score"] == 0.93
    assert added["status"] == "unreviewed"
    assert added["corrected_text"] is None
    assert added["polygon"] == BOX
    orders = [ln["reading_order"] for ln in lines]
    assert sorted(orders) == list(range(len(lines)))  # dense, no gaps or duplicates


def test_detect_box_returns_422_and_creates_no_line_when_nothing_found(client, image_id, monkeypatch):
    monkeypatch.setattr(dispatch, "_engine", FakeOcrEngine(recognize_quad_result=None))
    before = client.get(f"/api/images/{image_id}").json()["lines"]

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": BOX})

    assert resp.status_code == 422
    after = client.get(f"/api/images/{image_id}").json()["lines"]
    assert after == before


def test_detect_box_rejects_a_too_small_box_without_calling_the_engine(client, image_id, monkeypatch):
    engine = FakeOcrEngine(recognize_quad_result=Candidate("x", 0.9, "manual"))
    monkeypatch.setattr(dispatch, "_engine", engine)
    tiny = [[100.0, 100.0], [104.0, 100.0], [104.0, 104.0], [100.0, 104.0]]

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": tiny})

    assert resp.status_code == 400
    assert engine.recognize_quad_calls == []


class _NoManualBoxEngine:
    def warmup(self) -> None:
        """No model to build."""

    def run(self, image_path):
        raise NotImplementedError


def test_detect_box_returns_501_when_the_engine_has_no_recognize_quad(client, image_id, monkeypatch):
    monkeypatch.setattr(dispatch, "_engine", _NoManualBoxEngine())

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": BOX})

    assert resp.status_code == 501


def test_detect_box_404_for_an_unknown_image(client):
    resp = client.post("/api/images/9999/lines/detect-box", json={"polygon": BOX})
    assert resp.status_code == 404
