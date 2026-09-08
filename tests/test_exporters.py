import json
from pathlib import Path

from PIL import Image as PILImage


def _seed(client, upload, tmp_path) -> int:
    path = tmp_path / "page-1.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("export-me", [path])
    image = client.get(f"/api/batches/{batch['id']}/images").json()[0]
    detail = client.get(f"/api/images/{image['id']}").json()
    client.patch(f"/api/lines/{detail['lines'][0]['id']}", json={"corrected_text": "نص مصحح"})
    return batch["id"]


def test_jsonl_export_is_utf8_and_keeps_both_texts(client, upload, tmp_path, session_factory):
    from app.exporters import export_jsonl

    batch_id = _seed(client, upload, tmp_path)
    out = tmp_path / "out"
    with session_factory() as session:
        path = export_jsonl(session, batch_id, out)

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM
    assert "نص مصحح".encode() in raw  # literal Arabic, not \uXXXX
    record = json.loads(raw.decode("utf-8").splitlines()[0])
    assert record["filename"] == "page-1.png"
    assert [ln["reading_order"] for ln in record["lines"]] == [0, 1, 2, 3]
    assert record["lines"][0]["corrected_text"] == "نص مصحح"
    assert record["lines"][0]["rec_text"] != "نص مصحح"  # original preserved
    assert record["lines"][0]["final_text"] == "نص مصحح"
    assert len(record["lines"][0]["polygon"]) == 4


def test_txt_export_uses_final_text_in_reading_order(client, upload, tmp_path, session_factory):
    from app.exporters import export_txt

    batch_id = _seed(client, upload, tmp_path)
    out = tmp_path / "out"
    with session_factory() as session:
        directory = export_txt(session, batch_id, out)

    text_file = Path(directory) / "page-1.txt"
    lines = text_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "نص مصحح"
    assert lines[1] == "السطر"  # reading order preserved: rightmost of band 2
    assert len(lines) == 4
