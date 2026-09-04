def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_import_ocrs_every_image_and_serves_lines(client, source_dir):
    resp = client.post("/api/batches", json={"name": "batch-1", "source_dir": str(source_dir)})
    assert resp.status_code == 201
    batch = resp.json()
    assert batch["image_count"] == 2  # the .txt is ignored

    images = client.get(f"/api/batches/{batch['id']}/images").json()
    assert [i["status"] for i in images] == ["done", "done"]

    detail = client.get(f"/api/images/{images[0]['id']}").json()
    assert detail["width"] == 1000
    assert [ln["reading_order"] for ln in detail["lines"]] == [0, 1, 2, 3]
    assert detail["lines"][1]["rec_text"] == "السطر"  # rightmost of its band
    assert detail["lines"][1]["corrected_text"] is None
    assert len(detail["lines"][0]["polygon"]) == 4

    raw = client.get(f"/api/images/{images[0]['id']}/file")
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "image/png"


def test_reimport_same_folder_adds_nothing(client, source_dir):
    first = client.post("/api/batches", json={"name": "a", "source_dir": str(source_dir)}).json()
    second = client.post("/api/batches", json={"name": "b", "source_dir": str(source_dir)}).json()
    assert first["image_count"] == 2
    assert second["image_count"] == 0
    assert second["skipped_count"] == 2


def test_import_rejects_missing_dir(client, tmp_path):
    resp = client.post("/api/batches", json={"name": "x", "source_dir": str(tmp_path / "nope")})
    assert resp.status_code == 400
