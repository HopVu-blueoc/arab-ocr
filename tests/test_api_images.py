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
