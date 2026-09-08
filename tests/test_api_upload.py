import io

from PIL import Image as PILImage


def distinct_png(index: int) -> bytes:
    """Blank images of the same size are byte-identical and would deduplicate."""
    img = PILImage.new("RGB", (1000, 400), "white")
    img.putpixel((index, 0), (0, 0, 0))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_batch_is_created_empty_and_names_its_storage_prefix(client):
    resp = client.post("/api/batches", json={"name": "batch-1"})
    assert resp.status_code == 201
    batch = resp.json()
    assert batch["image_count"] == 0
    assert batch["source_dir"] == f"batch-{batch['id']}"


def test_uploaded_images_are_ocred_and_serve_their_lines(client, source_dir):
    batch = client.post("/api/batches", json={"name": "b"}).json()
    files = [
        ("files", (p.name, p.read_bytes(), "image/png")) for p in sorted(source_dir.glob("*.png"))
    ]
    resp = client.post(f"/api/batches/{batch['id']}/images", files=files)
    assert resp.status_code == 201
    assert resp.json() == {"imported": 2, "skipped": 0, "failed": []}

    images = client.get(f"/api/batches/{batch['id']}/images").json()
    assert [i["status"] for i in images] == ["done", "done"]
    assert [i["filename"] for i in images] == ["one.png", "two.png"]

    detail = client.get(f"/api/images/{images[0]['id']}").json()
    assert detail["width"] == 1000
    assert [ln["reading_order"] for ln in detail["lines"]] == [0, 1, 2, 3]
    assert detail["lines"][1]["rec_text"] == "السطر"  # rightmost of its band

    raw = client.get(f"/api/images/{images[0]['id']}/file")
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "image/png"
    assert raw.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_missing_object_still_reports_410(client, session_factory):
    """The API never exposes the storage key, so read it from the database."""
    from app.models import Image
    from app.storage import get_storage

    batch = client.post("/api/batches", json={"name": "gone"}).json()
    client.post(
        f"/api/batches/{batch['id']}/images",
        files=[("files", ("a.png", distinct_png(2), "image/png"))],
    )
    image_id = client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]

    with session_factory() as session:
        key = session.get(Image, image_id).path
    get_storage().delete(key)

    assert client.get(f"/api/images/{image_id}/file").status_code == 410


def test_reuploading_the_same_bytes_is_skipped_not_duplicated(client):
    first = client.post("/api/batches", json={"name": "a"}).json()
    second = client.post("/api/batches", json={"name": "b"}).json()
    payload = [("files", ("shot.png", distinct_png(3), "image/png"))]

    assert client.post(f"/api/batches/{first['id']}/images", files=payload).json()["imported"] == 1
    again = client.post(f"/api/batches/{second['id']}/images", files=payload).json()
    assert again == {"imported": 0, "skipped": 1, "failed": []}
    assert client.get(f"/api/batches/{second['id']}/images").json() == []


def test_one_bad_file_does_not_fail_the_whole_upload(client):
    batch = client.post("/api/batches", json={"name": "mixed"}).json()
    resp = client.post(
        f"/api/batches/{batch['id']}/images",
        files=[
            ("files", ("good.png", distinct_png(5), "image/png")),
            ("files", ("invoice.png", b"%PDF-1.7 not a png", "image/png")),
            ("files", ("notes.pdf", b"whatever", "application/pdf")),
        ],
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["imported"] == 1
    assert [f["filename"] for f in body["failed"]] == ["invoice.png", "notes.pdf"]
    assert "not a readable image" in body["failed"][0]["reason"]
    assert len(client.get(f"/api/batches/{batch['id']}/images").json()) == 1


def test_traversal_filename_stays_inside_the_batch_prefix(client, tmp_path):
    batch = client.post("/api/batches", json={"name": "evil"}).json()
    resp = client.post(
        f"/api/batches/{batch['id']}/images",
        files=[("files", ("../../escaped.png", distinct_png(7), "image/png"))],
    )
    assert resp.json()["imported"] == 1
    objects_root = tmp_path / "data" / "images"
    assert [p.name for p in objects_root.iterdir()] == [f"batch-{batch['id']}"]
    assert not (tmp_path / "escaped.png").exists()
    image = client.get(f"/api/batches/{batch['id']}/images").json()[0]
    assert image["filename"] == "escaped.png"


def test_upload_to_a_missing_batch_is_404(client):
    resp = client.post(
        "/api/batches/999/images",
        files=[("files", ("a.png", distinct_png(1), "image/png"))],
    )
    assert resp.status_code == 404


def test_oversize_file_is_reported_per_file(client, monkeypatch):
    # Patch the router's seam rather than the MAX_UPLOAD_MB env var: the
    # `client` fixture has already built the app and warmed get_settings'
    # lru_cache, so clearing it mid-test to change one field is a trap.
    from app.routers import batches

    monkeypatch.setattr(batches, "_max_upload_bytes", lambda: 64)
    batch = client.post("/api/batches", json={"name": "tiny-cap"}).json()
    resp = client.post(
        f"/api/batches/{batch['id']}/images",
        files=[("files", ("big.png", distinct_png(9), "image/png"))],
    )
    assert resp.status_code == 201
    assert resp.json()["imported"] == 0
    assert "larger than" in resp.json()["failed"][0]["reason"]
