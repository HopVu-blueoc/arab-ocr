"""The upload path must not hold SQLite's single write lock across storage I/O.

WAL allows one writer at a time. The old code opened the write transaction on
the first dedupe SELECT (which autoflushed pending inserts) and then held it
across every storage.put(), so a concurrent upload or an OCR worker commit
queued behind it and could exhaust busy_timeout.
"""

import pytest
from sqlmodel import select

from app.models import Batch, Image
from tests.test_api_upload import distinct_png


def test_no_write_lock_is_held_during_storage_put(client, session_factory, monkeypatch):
    """A second connection must be able to commit while an upload is storing.

    This is the behavioural proof; asserting on code shape would pass just as
    happily with the transaction held open.
    """
    from app import storage as storage_module

    storage = storage_module.get_storage()
    committed_during_put = []

    original_put = storage.put

    def put_and_probe(key, path):
        result = original_put(key, path)
        # A different connection, mid-upload. Blocks if the request still
        # holds the write lock, and fails once busy_timeout expires.
        with session_factory() as probe:
            probe.add(Batch(name="probe", source_dir="probe"))
            probe.commit()
            committed_during_put.append(key)
        return result

    monkeypatch.setattr(storage, "put", put_and_probe)

    batch = client.post("/api/batches", json={"name": "upload"}).json()
    resp = client.post(
        f"/api/batches/{batch['id']}/images",
        files=[
            ("files", ("a.png", distinct_png(1), "image/png")),
            ("files", ("b.png", distinct_png(2), "image/png")),
        ],
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["imported"] == 2
    assert len(committed_during_put) == 2, "a concurrent write could not get through"


def test_duplicate_bytes_in_one_request_are_skipped_once(client):
    batch = client.post("/api/batches", json={"name": "dupes"}).json()
    same = distinct_png(7)

    body = client.post(
        f"/api/batches/{batch['id']}/images",
        files=[
            ("files", ("first.png", same, "image/png")),
            ("files", ("second.png", same, "image/png")),
        ],
    ).json()

    assert body == {"imported": 1, "skipped": 1, "failed": []}
    assert len(client.get(f"/api/batches/{batch['id']}/images").json()["items"]) == 1


def test_insert_or_existing_survives_a_racing_writer(client, session_factory):
    """The real race: another connection commits the same sha256 after the
    pre-check passes but before the insert flushes.

    The savepoint has to absorb that IntegrityError. Without it the exception
    poisons the whole transaction and every other file in the request dies
    with it.
    """
    from app.uploads import StoredUpload, build_image, insert_or_existing

    batch = client.post("/api/batches", json={"name": "racing"}).json()
    stored = StoredUpload(
        key=f"batch-{batch['id']}/deadbeef.png",
        sha256="deadbeef" * 8,
        width=10,
        height=10,
        display_name="racing.png",
    )

    with session_factory() as session:
        image = build_image(batch["id"], stored)
        original_add = session.add

        def add_then_let_a_rival_win(obj):
            original_add(obj)
            # Fires once, from inside the savepoint: pre-check has already
            # passed, flush has not yet run.
            session.add = original_add
            with session_factory() as rival:
                rival.add(build_image(batch["id"], stored))
                rival.commit()

        session.add = add_then_let_a_rival_win

        row, inserted = insert_or_existing(session, image)

        assert inserted is False, "the racing insert should have been detected"
        assert row.sha256 == stored.sha256
        # The transaction is still usable - that is the whole point.
        session.add(build_image(batch["id"], StoredUpload(
            key=f"batch-{batch['id']}/other.png",
            sha256="feedface" * 8,
            width=10,
            height=10,
            display_name="other.png",
        )))
        session.commit()

    with session_factory() as check:
        assert len(check.exec(select(Image).where(Image.sha256 == stored.sha256)).all()) == 1
        assert len(check.exec(select(Image).where(Image.sha256 == "feedface" * 8)).all()) == 1


def test_a_duplicate_does_not_fail_the_other_files_in_the_request(client):
    """A duplicate is one skipped file, not a failed upload."""
    duplicate = distinct_png(31)
    fresh = distinct_png(32)

    first = client.post("/api/batches", json={"name": "winner"}).json()
    client.post(
        f"/api/batches/{first['id']}/images",
        files=[("files", ("winner.png", duplicate, "image/png"))],
    )

    second = client.post("/api/batches", json={"name": "loser"}).json()
    body = client.post(
        f"/api/batches/{second['id']}/images",
        files=[
            ("files", ("dupe.png", duplicate, "image/png")),
            ("files", ("fresh.png", fresh, "image/png")),
        ],
    ).json()

    assert body["imported"] == 1, "the unrelated file should still have landed"
    assert body["skipped"] == 1
    assert body["failed"] == []


def test_a_deduped_upload_keeps_the_winners_object(client, session_factory):
    """Re-uploading into another batch must not delete the live object."""
    from app import storage as storage_module

    data = distinct_png(41)
    first = client.post("/api/batches", json={"name": "first"}).json()
    client.post(
        f"/api/batches/{first['id']}/images",
        files=[("files", ("a.png", data, "image/png"))],
    )

    with session_factory() as session:
        winner_key = session.exec(select(Image)).first().path

    second = client.post("/api/batches", json={"name": "second"}).json()
    body = client.post(
        f"/api/batches/{second['id']}/images",
        files=[("files", ("a.png", data, "image/png"))],
    ).json()
    assert body["skipped"] == 1

    # The winner's bytes must still be readable.
    storage = storage_module.get_storage()
    with storage.open(winner_key) as handle:
        assert handle.read(8) != b""


def test_orphan_cleanup_happens_after_the_commit(client, monkeypatch):
    """Deleting before commit could remove bytes a surviving row points at."""
    from app import storage as storage_module

    storage = storage_module.get_storage()
    events: list[str] = []

    original_delete = storage.delete

    def record_delete(key):
        events.append("delete")
        return original_delete(key)

    monkeypatch.setattr(storage, "delete", record_delete)

    data = distinct_png(51)
    first = client.post("/api/batches", json={"name": "one"}).json()
    client.post(
        f"/api/batches/{first['id']}/images", files=[("files", ("a.png", data, "image/png"))]
    )

    from sqlalchemy import event as sa_event

    from app.db import get_engine

    def on_commit(_conn):
        events.append("commit")

    sa_event.listen(get_engine(), "commit", on_commit)
    try:
        second = client.post("/api/batches", json={"name": "two"}).json()
        client.post(
            f"/api/batches/{second['id']}/images",
            files=[("files", ("a.png", data, "image/png"))],
        )
    finally:
        sa_event.remove(get_engine(), "commit", on_commit)

    assert "delete" in events, "the orphaned duplicate object was never cleaned up"
    assert events.index("commit") < events.index("delete"), (
        "storage cleanup ran before the transaction committed"
    )


@pytest.mark.parametrize("failing_index", [0, 1])
def test_a_failed_commit_leaves_no_orphaned_objects(client, monkeypatch, failing_index):
    """If the write transaction dies, this request's objects are garbage."""
    from app import storage as storage_module
    from app.routers import batches as batches_router

    storage = storage_module.get_storage()
    deleted: list[str] = []
    original_delete = storage.delete

    def record_delete(key):
        deleted.append(key)
        return original_delete(key)

    monkeypatch.setattr(storage, "delete", record_delete)

    def explode(*_args, **_kwargs):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(batches_router, "insert_or_existing", explode)

    batch = client.post("/api/batches", json={"name": "doomed"}).json()
    with pytest.raises(RuntimeError):
        client.post(
            f"/api/batches/{batch['id']}/images",
            files=[("files", ("a.png", distinct_png(60 + failing_index), "image/png"))],
        )

    assert len(deleted) == 1, "the stored object was left behind after a failed upload"
