import pytest
from PIL import Image as PILImage


@pytest.fixture
def image_id(client, upload, tmp_path) -> int:
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("b", [path])
    return client.get(f"/api/batches/{batch['id']}/images").json()["items"][0]["id"]


def test_approve_a_done_image(client, image_id):
    body = client.patch(f"/api/images/{image_id}", json={"status": "approved"}).json()
    assert body["status"] == "approved"


def test_cannot_approve_a_failed_image(client, image_id, session_factory):
    from app.models import Image, ImageStatus

    with session_factory() as session:
        image = session.get(Image, image_id)
        image.status = ImageStatus.failed
        session.add(image)
        session.commit()

    resp = client.patch(f"/api/images/{image_id}", json={"status": "approved"})
    assert resp.status_code == 409


def test_patch_unknown_image_is_404(client):
    assert client.patch("/api/images/9999", json={"status": "approved"}).status_code == 404


@pytest.mark.parametrize("status", ["failed", "queued", "running", "pending"])
def test_patch_rejects_every_status_but_approved(client, image_id, status):
    """Only approving is a reviewer action. Anything else here would let a
    client walk a done image back to failed and then use /retry to bypass
    the generation guard that protects reviewer corrections."""
    resp = client.patch(f"/api/images/{image_id}", json={"status": status})
    assert resp.status_code == 422


def test_retry_a_failed_image_requeues_it(client, image_id, session_factory):
    from app.models import Image, ImageStatus

    with session_factory() as session:
        image = session.get(Image, image_id)
        image.status = ImageStatus.failed
        image.error = "boom"
        session.add(image)
        session.commit()

    resp = client.post(f"/api/images/{image_id}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    # JOB_BACKEND=inline in tests: enqueue_image runs synchronously, so by
    # the time this responds the fake engine has already produced a result.
    assert body["status"] == "done"


def test_cannot_retry_a_non_failed_image(client, image_id):
    resp = client.post(f"/api/images/{image_id}/retry")
    assert resp.status_code == 409


def test_retry_unknown_image_is_404(client):
    assert client.post("/api/images/9999/retry").status_code == 404


def test_retry_bumps_generation_so_a_stale_task_cannot_reprocess(client, image_id, session_factory):
    """End-to-end version of the P1 #4 scenario: after retry, a task carrying
    the pre-retry generation must not be able to touch the row, even though
    it targets the same image_id and the row is (briefly, or by then already
    finished) queued again."""
    from app.models import Image, ImageStatus
    from app.ocr.fake_engine import FakeOcrEngine
    from app.worker.tasks import run_ocr

    with session_factory() as session:
        image = session.get(Image, image_id)
        image.status = ImageStatus.failed
        session.add(image)
        session.commit()
        stale_generation = image.ocr_generation

    resp = client.post(f"/api/images/{image_id}/retry")
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"  # inline backend already ran it

    with session_factory() as session:
        image = session.get(Image, image_id)
        assert image.ocr_generation == stale_generation + 1
        lines_before = image.updated_at

        engine = FakeOcrEngine()
        run_ocr(session, image_id, engine, stale_generation)  # the stale message

        session.refresh(image)
        assert len(engine.calls) == 0, "a stale-generation task reprocessed the image"
        assert image.updated_at == lines_before
        assert image.status is ImageStatus.done
