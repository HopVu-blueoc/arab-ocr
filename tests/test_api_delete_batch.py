import io

from PIL import Image as PILImage


def distinct_png(index: int) -> bytes:
    """Blank images of the same size are byte-identical and would deduplicate."""
    img = PILImage.new("RGB", (1000, 400), "white")
    img.putpixel((index, 0), (0, 0, 0))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def test_delete_removes_batch_images_lines_and_stored_objects(client, upload, tmp_path):
    from sqlmodel import Session, select

    from app.db import get_engine
    from app.models import Batch, Image, Line
    from app.storage import get_storage

    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("to-delete", [path])
    image = client.get(f"/api/batches/{batch['id']}/images").json()[0]
    detail = client.get(f"/api/images/{image['id']}").json()
    assert len(detail["lines"]) > 0  # sanity: the fake engine produced lines

    storage = get_storage()
    with Session(get_engine()) as session:
        key = session.get(Image, image["id"]).path
    assert storage.exists(key)

    resp = client.delete(f"/api/batches/{batch['id']}")
    assert resp.status_code == 204

    assert client.get(f"/api/batches/{batch['id']}").status_code == 404
    assert not storage.exists(key)

    with Session(get_engine()) as session:
        assert session.get(Batch, batch["id"]) is None
        assert session.get(Image, image["id"]) is None
        assert session.exec(select(Line).where(Line.image_id == image["id"])).all() == []


def test_deleting_one_batch_does_not_touch_another(client, upload, tmp_path):
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    PILImage.new("RGB", (1000, 400), "white").save(p1)
    PILImage.new("RGB", (1000, 400), "white").putpixel((5, 5), (0, 0, 0))
    img2 = PILImage.new("RGB", (1000, 400), "white")
    img2.putpixel((5, 5), (0, 0, 0))
    img2.save(p2)

    batch1, _ = upload("keep-me", [p1])
    batch2, _ = upload("delete-me", [p2])

    resp = client.delete(f"/api/batches/{batch2['id']}")
    assert resp.status_code == 204

    assert client.get(f"/api/batches/{batch1['id']}").status_code == 200
    assert len(client.get(f"/api/batches/{batch1['id']}/images").json()) == 1


def test_delete_unknown_batch_is_404(client):
    resp = client.delete("/api/batches/999")
    assert resp.status_code == 404


def test_deleted_batch_no_longer_lists(client, upload, tmp_path):
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("gone-from-list", [path])

    client.delete(f"/api/batches/{batch['id']}")

    ids = [b["id"] for b in client.get("/api/batches").json()]
    assert batch["id"] not in ids
