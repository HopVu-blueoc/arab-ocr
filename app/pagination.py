"""Keyset pagination helpers.

Keyset rather than LIMIT/OFFSET for three reasons:

* OFFSET 10000 makes the database walk and discard 10,000 index entries. That
  cost grows with depth, which is the thing being fixed here.
* Both paged lists are polled, and batches are inserted at the *head* of
  `created_at DESC`. Under OFFSET every insert shifts each following page, so
  a client paging through a live list sees rows twice or not at all.
* The planned per-user filter is one more `AND` in a keyset predicate, with
  the index simply becoming (owner_id, created_at, id). An offset's meaning
  changes entirely when a filter is added.

The cursor is opaque so it can gain an HMAC or an owner scope later without a
client change. It deliberately encodes only sort-key values, never an owner -
the server derives that from the session, so a leaked cursor cannot reach
another user's rows.
"""

import base64
import binascii
import json
from typing import Any

from fastapi import HTTPException


class InvalidCursor(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=400, detail="invalid cursor")


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, *, expected: tuple[str, ...]) -> dict[str, Any]:
    """Decode a cursor, or raise 400. Never trust it to be well-formed."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor from exc

    if not isinstance(payload, dict) or set(payload) != set(expected):
        raise InvalidCursor
    return payload
