# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Stop is always accepted, idempotent and cannot fail, from every state."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from spark_iis.store import TERMINAL, State
from tests.conftest import make_world

ZERO = {"visual_brightness": 0.0, "audio_level_dba": 0.0, "haptic_amplitude": 0.0}
ACTIONS = st.lists(st.sampled_from(["arm", "start", "beat", "wait", "frame", "badframe", "dim", "refuse"]),
                   max_size=12)


def _drive(w, sid, actions):
    t = 0
    for a in actions:
        if a == "arm":
            w.arm(sid)
        elif a == "start":
            w.start(sid)
        elif a == "beat":
            w.clock.advance(100)
            w.beat(sid)
        elif a == "wait":
            w.clock.advance(700)
        elif a in ("frame", "badframe"):
            t += 100
            w.send(sid, w.frame(sid, t, [float("nan")] * 32 if a == "badframe" else None))
        elif a == "dim":
            w.client.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "dim"})
        elif a == "refuse":
            w.store.participants[w.pid].consents[-1]["withdrawn"] = True


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(actions=ACTIONS, body=st.sampled_from([None, b"", b"{}", b"garbage", b'{"force": false}']))
def test_stop_from_any_reachable_state(actions, body):
    w = make_world()
    sid = w.session()
    _drive(w, sid, actions)
    before = w.store.sessions[sid].state
    r = w.client.post(f"/v1/sessions/{sid}:stop", content=body)
    assert r.status_code == 200
    j = r.json()
    assert j["stopped"] is True and j["output"] == ZERO
    s = w.store.sessions[sid]
    assert s.state in TERMINAL
    assert s.state == (before if before in TERMINAL else State.STOPPED)
    assert w.store.output(s) == ZERO
    again = w.client.post(f"/v1/sessions/{sid}:stop").json()
    assert again["state"] == j["state"] and again["already_terminal"] is True


def test_stop_every_state_explicitly(world):
    w = world
    states = {}
    sid = w.session()
    states["CREATED"] = sid
    sid = w.session(pid=w.new_participant())
    w.arm(sid)
    states["ARMED"] = sid
    states["RUNNING"] = w.running(pid=w.new_participant())
    for name, sid in states.items():
        assert w.state(sid) == name
        r = w.client.post(f"/v1/sessions/{sid}:stop")
        assert r.status_code == 200 and r.json()["state"] == "STOPPED"
    # SCREENED and CONSENTED are transient inside :arm; stop them through the store directly.
    for target in (State.SCREENED, State.CONSENTED):
        sid = w.session(pid=w.new_participant())
        s = w.store.sessions[sid]
        w.store._transition(s, State.SCREENED, w.store.now(), "test")
        if target == State.CONSENTED:
            w.store._transition(s, State.CONSENTED, w.store.now(), "test")
        assert w.store.stop(sid)["state"] == "STOPPED"


def test_stop_unknown_session_still_succeeds(world):
    r = world.client.post("/v1/sessions/s-ffffffffffff:stop", json={"anything": 1})
    assert r.status_code == 200 and r.json() == {"sid": "s-ffffffffffff", "known": False, "state": None,
                                                 "stopped": True, "output": ZERO}


def test_stop_survives_internal_error(world, monkeypatch):
    sid = world.running()

    def boom(*_a, **_k):
        raise RuntimeError("simulated fault")

    monkeypatch.setattr(world.store, "tick", boom)
    r = world.client.post(f"/v1/sessions/{sid}:stop")
    assert r.status_code == 200 and r.json()["forced"] is True and r.json()["state"] == "STOPPED"
    assert world.store.sessions[sid].state == State.STOPPED
    r = world.store.stop("s-unknown")
    assert r["known"] is False
