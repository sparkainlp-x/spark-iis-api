# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""The residual latch fires on bad data: NaN, inf, wrong length, malformed frames, stale reference, missing sensor."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from spark_iis.latch import BLOCK_WIDTH, DIM_THRESHOLD, evaluate_frame, latch_score, residual_max
from tests.conftest import PROBLEM

REF = {"c": [0.0] * BLOCK_WIDTH}


def ev(frame, **kw):
    args = {"sid": "s", "channels": REF, "last_t_ms": {}, "reference_fresh": True}
    args.update(kw)
    return evaluate_frame(frame, **args)


def good(**over):
    f = {"sid": "s", "t_ms": 1, "channel": "c", "x": [0.0] * BLOCK_WIDTH, "tag": "SYNTHETIC"}
    f.update(over)
    return f


@pytest.mark.parametrize("frame,reason", [
    ([1, 2], "invalid_frame"),
    ({"sid": "s"}, "invalid_frame"),
    (good(extra=1), "invalid_frame"),
    (good(sid="other"), "invalid_frame"),
    (good(channel="zz"), "unknown_channel"),
    (good(channel=["c"]), "unknown_channel"),
    (good(t_ms=-1), "invalid_frame"),
    (good(t_ms=True), "invalid_frame"),
    (good(t_ms=1.5), "invalid_frame"),
    (good(tag="MEASURED"), "invalid_frame"),
    (good(tag=["SYNTHETIC"]), "invalid_frame"),
    (good(x="0" * 32), "invalid_frame"),
    (good(x=[0.0] * 31), "invalid_frame"),
    (good(x=[0.0] * 33), "invalid_frame"),
    (good(x=[0.0] * 31 + [float("nan")]), "invalid_frame"),
    (good(x=[float("inf")] + [0.0] * 31), "invalid_frame"),
    (good(x=[-float("inf")] + [0.0] * 31), "invalid_frame"),
    (good(x=[True] + [0.0] * 31), "invalid_frame"),
    (good(x=["1"] + [0.0] * 31), "invalid_frame"),
    (good(x=[10**400] + [0.0] * 31), "invalid_frame"),
    (good(x=[1e308, -1e308] + [0.0] * 30), "invalid_frame"),
])
def test_defects_latch(frame, reason):
    r = ev(frame)
    assert r.latched and r.reason == reason


def test_replayed_and_stale():
    assert ev(good(t_ms=5), last_t_ms={"c": 5}).latched
    r = ev(good(), reference_fresh=False)
    assert r.latched and r.reason == "stale_reference"


def test_score_formula():
    x = [0.0] * 31 + [1.0]
    peak, rms, mean = 1.0, math.sqrt(1 / 32), 1 / 32
    assert latch_score(x, [0.0] * 32) == pytest.approx(0.45 * peak + 0.35 * rms + 0.20 * mean)
    assert residual_max(x, [0.0] * 32) == 1.0


@given(st.lists(st.floats(-1, 1), min_size=32, max_size=32))
def test_actions_follow_thresholds(x):
    r = ev(good(x=x))
    s = latch_score(x, [0.0] * 32)
    assert r.S == s
    assert r.action == ("latch" if s >= 0.5 else "dim" if s >= DIM_THRESHOLD else "none")


@given(st.lists(st.one_of(st.floats(allow_nan=True, allow_infinity=True), st.booleans(), st.text(max_size=2),
                          st.none()), min_size=0, max_size=40))
def test_any_non_finite_or_wrong_shape_latches(x):
    valid = (len(x) == 32 and all(isinstance(v, float) and math.isfinite(v) for v in x))
    r = ev(good(x=x))
    if not valid:
        assert r.latched


@pytest.mark.parametrize("raw", [
    b'{"sid": "SID", "t_ms": 100, "channel": "oes-0", "x": [NaN' + b', 0' * 31 + b'], "tag": "SYNTHETIC"}',
    b'{"sid": "SID", "t_ms": 100, "channel": "oes-0", "x": [Infinity' + b', 0' * 31 + b'], "tag": "SYNTHETIC"}',
    b'{"sid": "SID", "t_ms": 100, "channel": "oes-0", "x": [0, 0], "tag": "SYNTHETIC"}',
    b"not json",
    b"[" * 100_000,
    b" " * 70_000,
])
def test_api_latches_on_bad_frames(world, raw):
    sid = world.running()
    world.clock.advance(100)
    r = world.client.post(f"/v1/sessions/{sid}/telemetry", content=raw.replace(b"SID", sid.encode()),
                          headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["code"] == "SAFETY_LATCH" and body["state"] == "LATCHED" and body["latch"]["action"] == "stop"
    assert world.store.output(world.store.sessions[sid])["visual_brightness"] == 0.0
    assert len(world.store.sessions[sid].telemetry_hashes) == 1


def test_api_stale_reference_latches(world):
    sid = world.running()
    cal = {"date": "2026-10-19", "instrument": "SIMULATED", "tag": "SYNTHETIC", "reference": {"oes-0": [0.0] * 32}}
    world.client.post("/v1/devices/sim-sensor/calibrations", json=cal)
    world.clock.advance(100)
    r = world.send(sid, world.frame(sid, 100))
    assert r.status_code == 422 and r.json()["rule"] == "stale_reference"


def test_api_valid_frame_dim_and_not_running(world):
    sid = world.running()
    world.clock.advance(100)
    r = world.send(sid, world.frame(sid, 100))
    assert r.status_code == 200 and r.json()["action"] == "none" and r.json()["S"] == 0.0
    world.clock.advance(100)
    r = world.send(sid, world.frame(sid, 200, [v + 0.4 for v in world.reference]))
    assert r.json()["action"] == "dim" and r.json()["dim_factor"] == 0.5
    world.client.post(f"/v1/sessions/{sid}:stop")
    r = world.send(sid, world.frame(sid, 300))
    assert r.status_code == 409 and r.json()["code"] == "SESSION_STATE"
    assert world.send("s-000000000000", world.frame(sid, 300)).status_code == 404


def test_frames_with_wrong_channel_on_sessions_without_sensor(world):
    sid = world.running(devices=["sim-display", "sim-audio"])
    world.clock.advance(100)
    assert world.send(sid, world.frame(sid, 100)).json()["rule"] == "unknown_channel"
