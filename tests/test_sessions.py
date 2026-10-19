# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Session state machine, ARM re-checks, start confirmation, interactions, watchdog deadlines."""

from __future__ import annotations

import pytest

from spark_iis.store import TERMINAL, TRANSITIONS, State
from tests.conftest import PROBLEM


def _rules(r):
    assert r.headers["content-type"].startswith(PROBLEM), r.text
    return {v["rule"] for v in r.json()["violations"]}


def test_happy_path_walks_every_state(world):
    sid = world.session()
    assert world.state(sid) == "CREATED"
    assert world.arm(sid).json()["state"] == "ARMED"
    r = world.start(sid)
    assert r.json()["state"] == "RUNNING"
    path = [t["to"] for t in world.store.sessions[sid].transitions]
    assert path == ["SCREENED", "CONSENTED", "ARMED", "RUNNING"]
    out = r.json()["output"]
    assert out["visual_brightness"] == 0.0 and out["audio_level_dba"] == 0.0  # fades start at zero
    world.clock.advance(400)
    world.beat(sid)
    out = world.client.get(f"/v1/sessions/{sid}").json()["output"]
    assert 0 < out["visual_brightness"] <= 0.5 and 0 < out["audio_level_dba"] <= 45.0


def test_transition_table_is_closed():
    assert all(not TRANSITIONS[s] for s in TERMINAL)
    assert State.RUNNING not in TRANSITIONS[State.CREATED]


@pytest.mark.parametrize("missing,rule", [("screening", "SCREENING_MISSING"), ("consent", "CONSENT_MISSING"),
                                          ("preferences", "PREFERENCES_MISSING")])
def test_arm_refuses_without_prerequisites(world, missing, rule):
    pid = world.client.post("/v1/participants", json={}).json()["participant_id"]
    if missing != "preferences":
        world.client.put(f"/v1/participants/{pid}/preferences", json={
            "input_methods": ["switch"], "max_brightness": 0.6, "sound_off": False, "captions": True, "pace": "slow"})
    if missing != "screening":
        world.client.post(f"/v1/participants/{pid}/screenings", json={
            "questionnaire_version": "0.1", "photosensitive_seizure_history": "no", "light_triggered_migraine": "no",
            "discomfort_with_flashing_or_patterns": "no", "sound_sensitivity": "no"})
    if missing != "consent":
        world.client.post(f"/v1/participants/{pid}/consents", json={
            "consent_version": "0.1", "plain_language_text_sha256": "b" * 64, "agreed": True, "input_used": "switch"})
    sid = world.session(pid=pid)
    r = world.arm(sid)
    assert r.status_code == 422 and rule in _rules(r) and r.json()["state"] == "REFUSED"
    assert world.arm(sid).status_code == 409  # REFUSED is terminal


def test_arm_checks_preferences_and_devices(world):
    pid = world.new_participant(max_brightness=0.2, sound_off=True)
    r = world.arm(world.session(pid=pid))
    assert {"BRIGHTNESS_PREFERENCE", "SOUND_OFF_PREFERENCE"} <= _rules(r)
    r = world.arm(world.session(devices=["sim-sensor"]))
    assert "MISSING_DEVICE" in _rules(r)
    world.client.post("/v1/devices", json={"device_id": "dim-display", "kind": "display", "can_actuate": True,
                                            "simulated": True, "limits": {"max_brightness": 0.1}})
    r = world.arm(world.session(devices=["dim-display", "sim-audio"]))
    assert {"DEVICE_UNCALIBRATED", "DEVICE_BRIGHTNESS"} <= _rules(r)


def test_arm_needs_created_and_no_concurrent_session(world):
    sid = world.running()
    assert world.arm(sid).status_code == 409
    other = world.session()
    assert "CONCURRENT_SESSION" in _rules(world.arm(other))


def test_start_requires_profile_input_and_armed(world):
    sid = world.session()
    assert world.start(sid).status_code == 409
    world.arm(sid)
    r = world.start(sid, "voice")
    assert r.status_code == 422 and "CONFIRMATION_INPUT" in _rules(r)
    assert world.state(sid) == "ARMED"
    assert world.start(sid, "gaze").status_code == 200


def test_start_refuses_if_calibration_changed_after_arm(world):
    sid = world.session()
    world.arm(sid)
    cal = {"date": "2026-10-19", "instrument": "SIMULATED", "tag": "SYNTHETIC",
           "reference": {"oes-0": [0.0] * 32}}
    assert world.client.post("/v1/devices/sim-sensor/calibrations", json=cal).status_code == 201
    r = world.start(sid)
    assert "STALE_CALIBRATION" in _rules(r) and world.state(sid) == "REFUSED"


def test_heartbeat_timeout_stops(world):
    sid = world.running()
    world.clock.advance(400)
    world.beat(sid)
    world.clock.advance(500)
    assert world.state(sid) == "RUNNING"
    world.clock.advance(1)
    s = world.client.get(f"/v1/sessions/{sid}").json()
    assert s["state"] == "STOPPED" and s["end_reason"] == "heartbeat_missed"
    assert s["output"] == {"visual_brightness": 0.0, "audio_level_dba": 0.0, "haptic_amplitude": 0.0}


def test_missing_sensor_latches(world):
    sid = world.running()
    for _ in range(6):
        world.clock.advance(200)
        world.beat(sid)
    s = world.store.sessions[sid]
    assert s.state == State.LATCHED and s.latch["reason"] == "missing_sensor"
    assert world.store.participants[world.pid].uncleared_latch == sid


def test_protocol_completes(world):
    sid = world.running()
    for t in range(200, 60001, 200):
        world.clock.advance(200)
        world.beat(sid)
        if t % 1000 == 0 and t < 60000:
            assert world.send(sid, world.frame(sid, t)).status_code == 200
    assert world.state(sid) == "COMPLETED"
    assert world.store.sessions[sid].end_reason == "protocol_complete"


def test_interactions(world):
    sid = world.running()
    c = world.client
    r = c.post(f"/v1/sessions/{sid}/interactions", json={"input": "gaze", "intent": "dim"})
    assert r.json()["dim_factor"] == 0.5
    r = c.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "acknowledge"})
    assert r.status_code == 200
    r = c.post(f"/v1/sessions/{sid}/interactions", json={"input": "voice", "intent": "dim"})
    assert "INPUT_NOT_IN_PROFILE" in _rules(r)
    r = c.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "comfort_rating"})
    assert "MISSING_VALUE" in _rules(r)
    r = c.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "dim", "comfort_rating": 3})
    assert "UNEXPECTED_FIELD" in _rules(r)
    r = c.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "comfort_rating",
                                                          "comfort_rating": 4})
    assert r.json()["state"] == "RUNNING"
    r = c.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "comfort_rating",
                                                          "comfort_rating": 2})
    assert r.json()["state"] == "STOPPED" and world.store.sessions[sid].end_reason == "comfort_rating_low"
    r = c.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "dim"})
    assert r.status_code == 409


def test_stop_intent_accepted_from_any_input_and_state(world):
    sid = world.session()
    r = world.client.post(f"/v1/sessions/{sid}/interactions", json={"input": "voice", "intent": "stop"})
    assert r.status_code == 200 and r.json()["state"] == "STOPPED"


def test_break_rule(world):
    w = world
    long = w.protocol(protocol_id="fade-long", steps=[
        {"modality": "visual", "pattern": "fade", "period_s": 8.0, "brightness_max": 0.5, "colour": "#4060a0",
         "field_fraction": 0.2, "duration_s": 700.0}])
    assert w.client.post("/v1/protocols", json=long).status_code == 201
    pid = w.new_participant()

    def run_for(ms):
        sid = w.running(protocol_id="fade-long", devices=["sim-display"], pid=pid)
        for _ in range(ms // 200):
            w.clock.advance(200)
            w.beat(sid)
        w.client.post(f"/v1/sessions/{sid}:stop")

    run_for(600_000)
    r = w.arm(w.session(protocol_id="fade-long", devices=["sim-display"], pid=pid))
    assert "BREAK_REQUIRED" in _rules(r)
    w.clock.advance(600_000)
    assert w.arm(w.session(protocol_id="fade-long", devices=["sim-display"], pid=pid)).status_code == 200


def test_clear_latch_flow(world):
    sid = world.running()
    world.clock.advance(100)
    world.send(sid, world.frame(sid, 100, [v + 1 for v in world.reference]))
    assert world.state(sid) == "LATCHED"
    other = world.session()
    assert "LATCH_NOT_CLEARED" in _rules(world.arm(other))
    body = {"operator_id": "op-1", "note": "checked synthetic rig"}
    r = world.client.post(f"/v1/sessions/{sid}:clear-latch", json=body)
    assert r.status_code == 200 and r.json()["state"] == "LATCHED"
    assert r.json()["latch_cleared"]["operator_id"] == "op-1"
    assert world.client.post(f"/v1/sessions/{sid}:clear-latch", json=body).status_code == 409
    assert world.arm(world.session()).status_code == 200
    assert world.client.post(f"/v1/sessions/{other}:clear-latch", json=body).status_code == 409
    assert any(e["kind"] == "latch_cleared" for e in world.store.audit.entries)


def test_simulated_output_shapes():
    from spark_iis.models import Protocol
    from spark_iis.store import nominal_output

    p = Protocol.model_validate({"protocol_id": "shapes", "version": "0.1.0", "mode": "wellness_demo", "claims": [],
                                 "evidence_tag": "SYNTHETIC", "steps": [
        {"modality": "visual", "pattern": "static", "brightness_max": 0.3, "colour": "#4060a0",
         "field_fraction": 0.1, "duration_s": 10.0},
        {"modality": "visual", "pattern": "flicker", "rate_hz": 2.0, "brightness_max": 0.9, "colour": "#4060a0",
         "field_fraction": 0.1, "duration_s": 10.0},
        {"modality": "audio", "pattern": "noise", "level_dba": 40.0, "fade_in_s": 0.0, "fade_out_s": 0.0,
         "duration_s": 10.0},
        {"modality": "haptic", "pattern": "pulse", "amplitude": 0.4, "pulse_on_s": 1.0, "pulse_off_s": 1.0,
         "duration_s": 10.0, "start_s": 2.0},
        {"modality": "haptic", "pattern": "continuous", "amplitude": 0.2, "duration_s": 5.0, "start_s": 20.0},
    ]})
    assert nominal_output(p, 0.5) == {"visual_brightness": 0.3, "audio_level_dba": 40.0, "haptic_amplitude": 0.0}
    assert nominal_output(p, 2.5)["haptic_amplitude"] == 0.4
    assert nominal_output(p, 3.5)["haptic_amplitude"] == 0.0
    assert nominal_output(p, 21.0) == {"visual_brightness": 0.0, "audio_level_dba": 0.0, "haptic_amplitude": 0.2}


def test_module_entry_point(tmp_path, monkeypatch):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["spark-iis", "demo", "--out", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("spark_iis", run_name="__main__")
    assert exc.value.code == 0
