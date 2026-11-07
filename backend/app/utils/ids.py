"""UUIDv7 (RFC 9562): time-ordered ids, so primary-key B-tree inserts stay append-mostly.

Monotonic within a process (RFC 9562 §6.2, method 1): ids generated in the same millisecond use
the 12-bit ``rand_a`` field as a counter, so ``ORDER BY id`` reflects creation order even for rows
inserted in one transaction (where ``now()`` is identical).
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_RAND_B_MASK = (1 << 62) - 1
_COUNTER_MAX = 0xFFF

_lock = threading.Lock()
_last_ms = -1
_counter = 0


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        unix_ms = time.time_ns() // 1_000_000
        if unix_ms > _last_ms:
            _last_ms = unix_ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x7FF  # random start, room to count up
        else:
            # Same millisecond (or the clock went backwards): keep the timestamp, bump the counter.
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1
                _counter = 0
            unix_ms = _last_ms
        counter = _counter

    rand_b = int.from_bytes(os.urandom(8), "big") & _RAND_B_MASK
    value = (unix_ms & ((1 << 48) - 1)) << 80  # 48-bit timestamp
    value |= 0x7 << 76  # version
    value |= counter << 64  # rand_a used as a monotonic counter (12 bits)
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return uuid.UUID(int=value)
