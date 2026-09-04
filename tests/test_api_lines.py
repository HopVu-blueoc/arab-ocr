import pytest
from PIL import Image as PILImage


@pytest.fixture
def client_with_lines(client, tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    PILImage.new("RGB", (1000, 400), "white").save(d / "a.png")
    batch = client.post("/api/batches", json={"name": "b", "source_dir": str(d)}).json()
    image_id = client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]
    return client, client.get(f"/api/images/{image_id}").json()


def test_correction_marks_line_edited_and_keeps_rec_text(client_with_lines):
    client, image = client_with_lines
    line = image["lines"][0]

    resp = client.patch(f"/api/lines/{line['id']}", json={"corrected_text": "مرحبا بالعالمين"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corrected_text"] == "مرحبا بالعالمين"
    assert body["final_text"] == "مرحبا بالعالمين"
    assert body["rec_text"] == line["rec_text"]
    assert body["status"] == "edited"


def test_clearing_correction_resets_to_unreviewed(client_with_lines):
    client, image = client_with_lines
    line_id = image["lines"][0]["id"]
    client.patch(f"/api/lines/{line_id}", json={"corrected_text": "x"})

    body = client.patch(f"/api/lines/{line_id}", json={"corrected_text": None}).json()
    assert body["corrected_text"] is None
    assert body["final_text"] == image["lines"][0]["rec_text"]
    assert body["status"] == "unreviewed"


def test_approving_a_line_without_editing(client_with_lines):
    client, image = client_with_lines
    body = client.patch(
        f"/api/lines/{image['lines'][0]['id']}", json={"status": "approved"}
    ).json()
    assert body["status"] == "approved"
    assert body["corrected_text"] is None


def test_patch_unknown_line_is_404(client_with_lines):
    client, _ = client_with_lines
    assert client.patch("/api/lines/9999", json={"status": "approved"}).status_code == 404
