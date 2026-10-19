# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Monotonic millisecond clocks. The server never records wall-clock time (privacy, determinism)."""

from __future__ import annotations

import time


class MonotonicClock:
    def now_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


class FakeClock:
    """Deterministic clock for tests and the seed-42 synthetic demo."""

    def __init__(self, start_ms: int = 0) -> None:
        self._now = int(start_ms)

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> int:
        if ms < 0:
            raise ValueError("time cannot go backwards")
        self._now += int(ms)
        return self._now
