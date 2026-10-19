# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from spark_iis import demo
from spark_iis.clock import FakeClock
from spark_iis.store import Store

PROBLEM = "application/problem+json"


@dataclass
class World:
    client: TestClient
    store: Store
    clock: FakeClock
    pid: str
    reference: list[float]

    def protocol(self, **overrides: Any) -> dict[str, Any]:
        p = copy.deepcopy(demo.CALM_PROTOCOL)
        p.update(overrides)
        return p

    def session(self, protocol_id: str = "fade-calm-01", devices: list[str] | None = None,
                pid: str | None = None) -> str:
        body = {"participant_id": pid or self.pid, "protocol_id": protocol_id,
                "device_ids": devices or ["sim-display", "sim-audio", "sim-sensor"]}
        r = self.client.post("/v1/sessions", json=body)
        assert r.status_code == 201, r.text
        return r.json()["sid"]

    def arm(self, sid: str) -> Any:
        return self.client.post(f"/v1/sessions/{sid}:arm")

    def start(self, sid: str, input_: str = "switch") -> Any:
        body = {"confirmation": {"input": input_, "intent": "start"}}
        return self.client.post(f"/v1/sessions/{sid}:start", json=body)

    def running(self, **kw: Any) -> str:
        sid = self.session(**kw)
        assert self.arm(sid).status_code == 200
        assert self.start(sid).status_code == 200
        return sid

    def frame(self, sid: str, t_ms: int, x: list[float] | None = None, **over: Any) -> dict[str, Any]:
        f = {"sid": sid, "t_ms": t_ms, "channel": demo.CHANNEL, "x": x if x is not None else list(self.reference),
             "tag": "SYNTHETIC"}
        f.update(over)
        return f

    def send(self, sid: str, frame: Any) -> Any:
        raw = json.dumps(frame)  # allows NaN/Infinity on purpose: the server must latch on them
        return self.client.post(f"/v1/sessions/{sid}/telemetry", content=raw,
                                headers={"content-type": "application/json"})

    def state(self, sid: str) -> str:
        return self.client.get(f"/v1/sessions/{sid}").json()["state"]

    def beat(self, sid: str) -> None:
        self.store.heartbeat(sid)

    def new_participant(self, **prefs: Any) -> str:
        pid = self.client.post("/v1/participants", json={}).json()["participant_id"]
        self.client.put(f"/v1/participants/{pid}/preferences", json={**demo.PREFERENCES, **prefs})
        self.client.post(f"/v1/participants/{pid}/screenings", json=demo.SCREENING)
        consent = {"consent_version": "0.1", "plain_language_text_sha256": "a" * 64, "agreed": True,
                   "input_used": "switch"}
        self.client.post(f"/v1/participants/{pid}/consents", json=consent)
        return pid


def make_world() -> World:
    client, store, clock, ctx = demo.build_world()
    return World(client, store, clock, ctx["pid"], ctx["reference"])


@pytest.fixture
def world() -> World:
    return make_world()
