import pytest
from PIL import Image as PILImage


@pytest.fixture
def image_id(client, tmp_path) -> int:
    d = tmp_path / "in"
    d.mkdir()
    PILImage.new("RGB", (1000, 400), "white").save(d / "a.png")
    batch = client.post("/api/batches", json={"name": "b", "source_dir": str(d)}).json()
    return client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]


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
