"""Deleting a batch must not hold a write transaction over storage I/O either.

Same defect class as the upload path: delete_batch materialised every image
row and made one storage call per image, all inside one open transaction.
"""

from sqlmodel import select

from app.models import Batch, Image, Line
from tests.test_api_upload import distinct_png


def _batch_with_images(client, count: int) -> dict:
    batch = client.post("/api/batches", json={"name": f"del-{count}"}).json()
    client.post(
        f"/api/batches/{batch['id']}/images",
        files=[
            ("files", (f"{i}.png", distinct_png(i), "image/png")) for i in range(count)
        ],
    )
    return batch


def test_delete_in_chunks_removes_every_row_and_object(client, session_factory, monkeypatch):
    from app.routers import batches as batches_router

    # Force several passes through the loop over a handful of images.
    monkeypatch.setattr(batches_router, "_DELETE_CHUNK", 2)

    batch = _batch_with_images(client, 5)
    with session_factory() as session:
        keys = [row.path for row in session.exec(select(Image)).all()]
    assert len(keys) == 5

    assert client.delete(f"/api/batches/{batch['id']}").status_code == 204

    from app import storage as storage_module

    storage = storage_module.get_storage()
    assert [k for k in keys if storage.exists(k)] == [], "objects survived the delete"

    with session_factory() as session:
        assert session.get(Batch, batch["id"]) is None
        assert session.exec(select(Image).where(Image.batch_id == batch["id"])).all() == []
        assert session.exec(select(Line)).all() == []


def test_no_write_lock_is_held_during_object_deletion(client, session_factory, monkeypatch):
    """A concurrent writer must get through while a batch is being deleted."""
    from app import storage as storage_module

    storage = storage_module.get_storage()
    batch = _batch_with_images(client, 3)

    committed = []
    original = storage.delete_many

    def delete_many_and_probe(keys):
        result = original(keys)
        with session_factory() as probe:
            probe.add(Batch(name="probe", source_dir="probe"))
            probe.commit()
            committed.append(len(keys))
        return result

    monkeypatch.setattr(storage, "delete_many", delete_many_and_probe)

    assert client.delete(f"/api/batches/{batch['id']}").status_code == 204
    assert committed, "storage cleanup never ran"


def test_a_storage_failure_still_removes_the_rows(client, session_factory, monkeypatch):
    """An orphaned object is recoverable; an orphaned row is not."""
    from app import storage as storage_module

    storage = storage_module.get_storage()
    batch = _batch_with_images(client, 2)

    def always_fail(_keys):
        raise RuntimeError("object store unavailable")

    monkeypatch.setattr(storage, "delete_many", always_fail)
    monkeypatch.setattr(storage, "delete", lambda _key: (_ for _ in ()).throw(RuntimeError("no")))

    assert client.delete(f"/api/batches/{batch['id']}").status_code == 204

    with session_factory() as session:
        assert session.get(Batch, batch["id"]) is None
        assert session.exec(select(Image).where(Image.batch_id == batch["id"])).all() == []


def test_deleting_one_batch_leaves_another_alone(client, session_factory):
    keep = _batch_with_images(client, 2)
    drop = _batch_with_images(client, 2)

    assert client.delete(f"/api/batches/{drop['id']}").status_code == 204

    with session_factory() as session:
        survivors = session.exec(select(Image).where(Image.batch_id == keep["id"])).all()
        assert len(survivors) == 2
        from app import storage as storage_module

        storage = storage_module.get_storage()
        for image in survivors:
            assert storage.exists(image.path), "an unrelated batch's object was deleted"
