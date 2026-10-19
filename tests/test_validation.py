# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Fail closed: unknown, missing or malformed fields are refused as problem+json with code SAFETY_ENVELOPE."""

from __future__ import annotations

import copy

import pytest

from spark_iis import demo
from spark_iis.boundary import BOUNDARY_HEADER, BOUNDARY_STATEMENT
from tests.conftest import PROBLEM

SECRET = "do-not-echo-7f3a"


def _refused(r, rule="FAIL_CLOSED_VALIDATION"):
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["code"] == "SAFETY_ENVELOPE" and body["rule"] == rule and body["status"] == 422
    assert {"type", "title", "detail", "instance"} <= set(body)
    assert SECRET not in r.text
    return body


def _protocol_variants():
    base = copy.deepcopy(demo.CALM_PROTOCOL)
    top = {**copy.deepcopy(base), "brainwave_target": SECRET}
    step = copy.deepcopy(base)
    step["steps"][0]["entrainment_hz"] = SECRET
    claim = copy.deepcopy(base)
    claim["claims"] = [{"text": "x", "source": "y", "tag": "REPORTED", "doi_hint": SECRET}]
    unit = copy.deepcopy(base)
    unit["steps"][1]["level_db_spl"] = 40.0
    nan = copy.deepcopy(base)
    nan["steps"][0]["brightness_max"] = SECRET
    modality = copy.deepcopy(base)
    modality["steps"][0]["modality"] = "olfactory"
    return [top, step, claim, unit, nan, modality]


@pytest.mark.parametrize("body", _protocol_variants())
def test_unknown_or_malformed_protocol_fields_refused(world, body):
    _refused(world.client.post("/v1/protocols", json=body))
    _refused(world.client.post("/v1/protocols/fade-calm-01:validate", json=body))


@pytest.mark.parametrize(
    "path,body",
    [
        ("/v1/participants", {"label": "a", "email": SECRET}),
        ("/v1/participants", {"name": SECRET}),
        ("/v1/devices", {"device_id": "d", "kind": "laser", "limits": {}, "can_actuate": True, "simulated": True}),
        ("/v1/devices", {"device_id": "d", "kind": "display", "limits": {"max_lux": 3}, "can_actuate": True,
                         "simulated": True}),
        ("/v1/devices", {"device_id": "d", "kind": "display", "limits": {}, "can_actuate": True, "simulated": False}),
        ("/v1/devices", {"device_id": "d", "kind": "display", "limits": {}, "can_actuate": "yes", "simulated": True}),
        ("/v1/devices/sim-display/calibrations", {"date": "2026-01-01", "instrument": "x", "tag": "MEASURED"}),
        ("/v1/sessions", {"participant_id": "p-000000000000", "protocol_id": "x", "device_ids": ["a"],
                          "priority": SECRET}),
    ],
)
def test_other_unknown_fields_refused(world, path, body):
    _refused(world.client.post(path, json=body))


def test_nested_unknown_fields_refused(world):
    pid = world.pid
    _refused(world.client.put(f"/v1/participants/{pid}/preferences", json={**demo.PREFERENCES, "x": SECRET}))
    _refused(world.client.put(f"/v1/participants/{pid}/preferences", json={**demo.PREFERENCES, "max_brightness": True}))
    _refused(world.client.post(f"/v1/participants/{pid}/screenings", json={**demo.SCREENING, "notes": SECRET}))
    _refused(world.client.post(f"/v1/participants/{pid}/consents",
                               json={"consent_version": "0.1", "plain_language_text_sha256": "a" * 64,
                                     "agreed": False, "input_used": "switch"}))
    sid = world.session()
    world.arm(sid)
    _refused(world.client.post(f"/v1/sessions/{sid}:start",
                               json={"confirmation": {"input": "switch", "intent": "start", "force": SECRET}}))
    _refused(world.client.post(f"/v1/sessions/{sid}:start", json={"confirmation": {"input": "telepathy",
                                                                                   "intent": "start"}}))
    _refused(world.client.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "brighten"}))


def test_nan_in_protocol_json_refused(world):
    raw = demo._dump(demo.CALM_PROTOCOL).replace('"brightness_max": 0.5', '"brightness_max": NaN')
    r = world.client.post("/v1/protocols", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_problem_json_for_not_found_and_method(world):
    for r, code in (
        (world.client.get("/v1/nowhere"), "NOT_FOUND"),
        (world.client.get("/v1/sessions/s-000000000000"), "NOT_FOUND"),
        (world.client.put("/v1/participants/p-000000000000/preferences", json=demo.PREFERENCES), "NOT_FOUND"),
        (world.client.delete("/v1/devices"), "METHOD_NOT_ALLOWED"),
    ):
        assert r.headers["content-type"].startswith(PROBLEM)
        assert r.json()["code"] == code


def test_boundary_header_on_every_response(world):
    for r in (world.client.get("/v1"), world.client.get("/v1/nowhere"),
              world.client.post("/v1/protocols", json={"x": 1}), world.client.post("/v1/sessions/zz:stop")):
        assert r.headers[BOUNDARY_HEADER] == BOUNDARY_STATEMENT


def test_info_lists_limits(world):
    body = world.client.get("/v1").json()
    assert body["boundary"] == BOUNDARY_STATEMENT and body["devices"] == "simulated only"
    assert body["limits"]["max_audio_dba"] == {"value": 70.0, "unit": "dB(A)", "tag": "TARGET"}
