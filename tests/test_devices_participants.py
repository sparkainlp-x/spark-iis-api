# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from tests.conftest import PROBLEM


def _device(**kw):
    d = {"device_id": "sim-x", "kind": "display", "limits": {"max_brightness": 0.5}, "can_actuate": True,
         "simulated": True}
    d.update(kw)
    return d


def _rules(r):
    assert r.status_code == 422 and r.headers["content-type"].startswith(PROBLEM)
    return {v["rule"] for v in r.json()["violations"]}


def test_device_rules(world):
    c = world.client
    assert "MEDICAL_DEVICE" in _rules(c.post("/v1/devices", json=_device(device_id="hbot-chamber-1")))
    assert "MEDICAL_DEVICE" in _rules(c.post("/v1/devices", json=_device(device_id="oxygen-valve")))
    assert "SENSOR_READ_ONLY" in _rules(c.post("/v1/devices", json=_device(kind="sensor", channels=["a"])))
    assert "MISSING_VALUE" in _rules(c.post("/v1/devices", json=_device(kind="sensor", can_actuate=False)))
    assert "UNEXPECTED_FIELD" in _rules(c.post("/v1/devices", json=_device(channels=["a"])))
    assert "MISSING_VALUE" in _rules(c.post("/v1/devices", json=_device(kind="haptic")))
    cal = {"date": "2026-10-01", "instrument": "SIMULATED", "tag": "REPORTED"}
    assert "CALIBRATION_INSTRUMENT" in _rules(c.post("/v1/devices", json=_device(calibration=cal)))
    ref = {**cal, "tag": "SYNTHETIC", "reference": {"a": [0.0] * 32}}
    assert "UNEXPECTED_FIELD" in _rules(c.post("/v1/devices", json=_device(calibration=ref)))
    sensor = _device(kind="sensor", can_actuate=False, channels=["a", "b"], calibration=ref)
    assert "MISSING_REFERENCE" in _rules(c.post("/v1/devices", json=sensor))
    assert c.post("/v1/devices", json=_device(device_id="sim-display")).status_code == 409


def test_device_disabled_until_calibrated(world):
    c = world.client
    r = c.post("/v1/devices", json=_device())
    assert r.status_code == 201 and r.json()["enabled"] is False
    cal = {"date": "2026-10-01", "instrument": "Lux meter LX-1 (serial 001)", "tag": "REPORTED"}
    r = c.post("/v1/devices/sim-x/calibrations", json=cal)
    assert r.status_code == 201 and r.json()["enabled"] is True
    assert c.post("/v1/devices/nope/calibrations", json=cal).status_code == 404
    bad = {**cal, "instrument": "none"}
    assert "CALIBRATION_INSTRUMENT" in _rules(c.post("/v1/devices/sim-x/calibrations", json=bad))
    unrun = {**cal, "tag": "UNRUN", "instrument": "UNRUN"}
    assert c.post("/v1/devices/sim-x/calibrations", json=unrun).json()["enabled"] is False
    listed = {d["device_id"]: d["enabled"] for d in c.get("/v1/devices").json()["devices"]}
    assert listed["sim-x"] is False and listed["sim-display"] is True


def test_participant_is_pseudonymous(world):
    r = world.client.post("/v1/participants", json={"label": "Room 3"})
    assert r.status_code == 201
    assert set(r.json()) == {"participant_id", "label"}
    assert r.json()["participant_id"].startswith("p-")


def test_screening_blocks_visual_modulation(world):
    pid = world.pid
    s = {"questionnaire_version": "0.1", "photosensitive_seizure_history": "unsure",
         "light_triggered_migraine": "no", "discomfort_with_flashing_or_patterns": "no", "sound_sensitivity": "no"}
    r = world.client.post(f"/v1/participants/{pid}/screenings", json=s)
    assert r.json()["visual_modulation_blocked"] is True
    sid = world.session()
    r = world.arm(sid)
    assert "VISUAL_MODULATION_BLOCKED" in _rules(r) and r.json()["state"] == "REFUSED"


def test_unknown_consent_and_participant(world):
    assert world.client.delete(f"/v1/participants/{world.pid}/consents/c-000000000000").status_code == 404
    assert world.client.post("/v1/participants/p-000000000000/screenings", json={}).status_code == 422


def test_duplicate_protocol_and_unknowns_on_session_create(world):
    c = world.client
    assert c.post("/v1/protocols", json=world.protocol()).status_code == 409
    base = {"participant_id": world.pid, "protocol_id": "fade-calm-01", "device_ids": ["sim-display"]}
    assert c.post("/v1/sessions", json={**base, "participant_id": "p-000000000000"}).status_code == 404
    assert c.post("/v1/sessions", json={**base, "protocol_id": "nope"}).status_code == 404
    assert c.post("/v1/sessions", json={**base, "device_ids": ["nope"]}).status_code == 404
    r = c.post("/v1/sessions", json={**base, "device_ids": ["sim-display", "sim-display"]})
    assert "DUPLICATE_DEVICE" in _rules(r)
    r = c.post("/v1/protocols/other:validate", json=world.protocol())
    assert r.json()["violations"][0]["rule"] == "ID_MISMATCH"
