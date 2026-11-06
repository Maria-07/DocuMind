"""UUIDv7 (RFC 9562): time-ordered ids, so primary-key B-tree inserts stay append-mostly."""

from __future__ import annotations

import os
import time
import uuid

_RAND_B_MASK = (1 << 62) - 1


def uuid7() -> uuid.UUID:
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits; 74 are used
    value = (unix_ms & ((1 << 48) - 1)) << 80  # 48-bit timestamp
    value |= 0x7 << 76  # version
    value |= ((rand >> 62) & 0xFFF) << 64  # rand_a (12 bits)
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand & _RAND_B_MASK  # rand_b (62 bits)
    return uuid.UUID(int=value)
