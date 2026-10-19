# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Append-only, SHA-256 hash-chained audit log (integrity checking, not tamper-proof storage)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "0" * 64


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha256(obj: Any) -> str:
    data = obj if isinstance(obj, bytes) else canonical(obj)
    return hashlib.sha256(data).hexdigest()


class AuditLog:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, t_ms: int, kind: str, **fields: Any) -> dict[str, Any]:
        prev = self.entries[-1]["hash"] if self.entries else GENESIS
        body = {"seq": len(self.entries), "t_ms": t_ms, "kind": kind, **fields, "prev": prev}
        entry = {**body, "hash": sha256(body)}
        self.entries.append(entry)
        return entry

    def verify(self) -> bool:
        prev = GENESIS
        for i, entry in enumerate(self.entries):
            body = {k: v for k, v in entry.items() if k != "hash"}
            if body.get("seq") != i or body.get("prev") != prev or sha256(body) != entry["hash"]:
                return False
            prev = entry["hash"]
        return True

    def for_subject(self, key: str, value: str) -> list[dict[str, Any]]:
        return [e for e in self.entries if e.get(key) == value]
