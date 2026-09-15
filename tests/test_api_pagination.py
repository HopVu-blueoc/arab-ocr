"""Pagination and the query-count ceiling on the list endpoints.

The defect these guard: listing batches ran one status aggregate per batch and
returned every batch ever created, so a 10k-batch database took 2.14s and
1.8MB per poll.
"""

from sqlalchemy import event

from app.db import get_engine


def _make_batches(client, count: int) -> list[int]:
    return [client.post("/api/batches", json={"name": f"b{i}"}).json()["id"] for i in range(count)]


class QueryCounter:
    """Counts SELECTs issued on the app's engine while active."""

    def __init__(self) -> None:
        self.selects = 0

    def _on_execute(self, _conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            self.selects += 1

    def __enter__(self):
        event.listen(get_engine(), "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *_exc):
        event.remove(get_engine(), "before_cursor_execute", self._on_execute)


def test_listing_cost_does_not_grow_with_the_number_of_batches(client):
    """The N+1 regression test: 3 batches and 8 batches must cost the same."""
    _make_batches(client, 3)
    with QueryCounter() as few:
        assert client.get("/api/batches").status_code == 200

    _make_batches(client, 5)
    with QueryCounter() as more:
        assert client.get("/api/batches").status_code == 200

    assert few.selects == more.selects, (
        f"listing 8 batches cost {more.selects} SELECTs vs {few.selects} for 3 - "
        "the per-batch count query is back"
    )


def test_batch_with_no_images_reports_zero_counts(client):
    """Batches absent from the aggregate still need their counts defaulted."""
    client.post("/api/batches", json={"name": "empty"})
    body = client.get("/api/batches").json()
    assert body["items"][0]["image_count"] == 0
    assert body["items"][0]["done_count"] == 0


def test_counts_are_correct(client, upload, tmp_path):
    from PIL import Image as PILImage

    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.png"
        img = PILImage.new("RGB", (1000, 400), "white")
        img.putpixel((i, 0), (0, 0, 0))  # distinct bytes, or dedupe drops them
        img.save(p)
        paths.append(p)
    batch, _ = upload("counted", paths)

    entry = next(b for b in client.get("/api/batches").json()["items"] if b["id"] == batch["id"])
    assert entry["image_count"] == 3
    assert entry["done_count"] == 3  # the fake engine completes them inline


def test_cursor_walk_yields_every_batch_exactly_once(client):
    created = set(_make_batches(client, 12))

    seen: list[int] = []
    cursor = None
    for _ in range(20):  # bounded, so a broken cursor loops the test out
        url = "/api/batches?limit=5" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url).json()
        seen.extend(b["id"] for b in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert cursor is None, "cursor never terminated"
    assert len(seen) == len(set(seen)), "a batch appeared on two pages"
    assert set(seen) == created


def test_batches_sharing_a_created_at_are_not_skipped(client, session_factory):
    """id breaks the tie; without it a page boundary silently loses a row."""
    from datetime import datetime

    from app.models import Batch

    ids = _make_batches(client, 4)
    # Naive on purpose: the column is DateTime without timezone, so SQLite
    # stores and returns naive values regardless of what utcnow() hands it.
    stamp = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001
    with session_factory() as session:
        for batch_id in ids:
            session.get(Batch, batch_id).created_at = stamp
        session.commit()

    seen: list[int] = []
    cursor = None
    for _ in range(10):
        url = "/api/batches?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url).json()
        seen.extend(b["id"] for b in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert sorted(seen) == sorted(ids)


def test_a_new_batch_mid_walk_does_not_shift_the_page(client):
    """What keyset buys over OFFSET: inserting at the head shifts nothing."""
    first_five = _make_batches(client, 5)

    page = client.get("/api/batches?limit=2").json()
    cursor = page["next_cursor"]
    client.post("/api/batches", json={"name": "inserted-at-head"})

    rest: list[int] = []
    while cursor is not None:
        body = client.get(f"/api/batches?limit=2&cursor={cursor}").json()
        rest.extend(b["id"] for b in body["items"])
        cursor = body["next_cursor"]

    walked = [b["id"] for b in page["items"]] + rest
    assert len(walked) == len(set(walked)), "a row was served twice"
    assert set(first_five) <= set(walked), "a pre-existing batch was skipped"


def test_limit_above_the_maximum_is_rejected(client):
    assert client.get("/api/batches?limit=201").status_code == 422
    assert client.get("/api/batches?limit=0").status_code == 422


def test_malformed_cursor_is_400(client):
    assert client.get("/api/batches?cursor=not-base64!!").status_code == 400
    # Valid base64, wrong shape.
    assert client.get("/api/batches?cursor=eyJ4IjoxfQ").status_code == 400


def test_images_paginate_by_filename(client, upload, tmp_path):
    from PIL import Image as PILImage

    paths = []
    for i, name in enumerate(["c.png", "a.png", "b.png"]):
        p = tmp_path / name
        img = PILImage.new("RGB", (1000, 400), "white")
        img.putpixel((i, 0), (0, 0, 0))
        img.save(p)
        paths.append(p)
    batch, _ = upload("images", paths)

    seen: list[str] = []
    cursor = None
    while True:
        url = f"/api/batches/{batch['id']}/images?limit=1" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url).json()
        seen.extend(i["filename"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == ["a.png", "b.png", "c.png"]


def test_images_with_the_same_filename_both_survive_paging(client, tmp_path):
    """filename is not unique - only sha256 is - so id has to break the tie."""
    from tests.test_api_upload import distinct_png

    batch = client.post("/api/batches", json={"name": "dupe-names"}).json()
    client.post(
        f"/api/batches/{batch['id']}/images",
        files=[
            ("files", ("same.png", distinct_png(11), "image/png")),
            ("files", ("same.png", distinct_png(12), "image/png")),
        ],
    )

    seen: list[int] = []
    cursor = None
    while True:
        url = f"/api/batches/{batch['id']}/images?limit=1" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url).json()
        seen.extend(i["id"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 2, "an image sharing a filename was lost at the page boundary"
    assert len(set(seen)) == 2


def test_image_cursor_does_not_leak_across_batches(client, tmp_path):
    from tests.test_api_upload import distinct_png

    one = client.post("/api/batches", json={"name": "one"}).json()
    two = client.post("/api/batches", json={"name": "two"}).json()
    client.post(
        f"/api/batches/{one['id']}/images",
        files=[("files", ("a.png", distinct_png(21), "image/png"))],
    )

    body = client.get(f"/api/batches/{one['id']}/images?limit=1").json()
    assert body["next_cursor"] is None  # only one image

    # A cursor minted on batch one, replayed against empty batch two.
    made_up = client.get(f"/api/batches/{two['id']}/images?limit=1").json()
    assert made_up["items"] == []
