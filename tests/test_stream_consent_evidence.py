# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""WebSocket heartbeat stream, consent withdrawal, evidence bundle, audit chain, demo reproducibility, CLI."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from spark_iis import cli, demo, evidence
from spark_iis.app import create_app
from spark_iis.audit import AuditLog
from spark_iis.boundary import BOUNDARY_STATEMENT
from spark_iis.clock import FakeClock, MonotonicClock
from spark_iis.store import IdFactory, State, Store
from tests.conftest import World

DEMO_DIR = Path(__file__).resolve().parent.parent / "examples" / "demo"


def _until(ws, kind):
    while (msg := ws.receive_json())["type"] != kind:
        pass
    return msg


def test_stream_heartbeat_keeps_session_alive(world):
    sid = world.running()
    with world.client.websocket_connect(f"/v1/sessions/{sid}/stream") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["boundary"] == BOUNDARY_STATEMENT
        assert snap["session"]["state"] == "RUNNING"
        for i in range(1, 8):
            world.clock.advance(400)
            ws.send_json({"type": "heartbeat"})
            assert _until(ws, "heartbeat_ack")["state"] == "RUNNING"
            if i % 2 == 0:
                world.send(sid, world.frame(sid, i * 400))
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "UNKNOWN_MESSAGE"
        ws.send_json({"type": "brighten"})
        assert ws.receive_json()["code"] == "UNKNOWN_MESSAGE"
        ws.send_json({"type": "stop"})
        ack = _until(ws, "stop_ack")
        assert ack["state"] == "STOPPED" and ack["stopped"] is True
        assert _until(ws, "closed")["state"] == "STOPPED"


def test_stream_pushes_heartbeat_timeout_and_closes(world):
    sid = world.running()
    with world.client.websocket_connect(f"/v1/sessions/{sid}/stream") as ws:
        ws.receive_json()
        world.clock.advance(501)
        events = []
        with pytest.raises(WebSocketDisconnect):
            while True:
                events.append(ws.receive_json())
        states = [e["event"].get("state") for e in events if e["type"] == "event"]
        assert "STOPPED" in states and events[-1]["type"] == "closed"


def test_stream_unknown_session(world):
    with world.client.websocket_connect("/v1/sessions/s-000000000000/stream") as ws:
        assert ws.receive_json()["code"] == "NOT_FOUND"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_consent_withdrawal_deletes_raw_keeps_hashes(world):
    sid = world.running()
    for t in (100, 200, 300):
        world.clock.advance(100)
        world.beat(sid)
        world.send(sid, world.frame(sid, t))
    s = world.store.sessions[sid]
    assert len(s.telemetry) == 3
    cid = world.store.participants[world.pid].consents[-1]["consent_id"]
    r = world.client.delete(f"/v1/participants/{world.pid}/consents/{cid}")
    assert r.json() == {"consent_id": cid, "withdrawn": True, "raw_frames_deleted": 3, "hashes_kept": True}
    assert s.state == State.STOPPED and s.end_reason == "consent_withdrawn"
    assert s.telemetry == [] and len(s.telemetry_hashes) == 3
    rep = world.client.get(f"/v1/sessions/{sid}/evidence").json()["report"]
    assert rep["frames"]["raw_retained"]["value"] == 0 and rep["frames"]["received"]["value"] == 3
    assert rep["frames"]["raw_deleted_by_consent_withdrawal"] is True
    assert any(e["kind"] == "consent_withdrawn" for e in world.store.audit.entries)
    r = world.arm(world.session())
    assert "CONSENT_MISSING" in {v["rule"] for v in r.json()["violations"]}


def test_evidence_bundle(world, monkeypatch):
    sid = world.running()
    monkeypatch.setenv("SPARK_IIS_GIT_COMMIT", "abc123")
    b = world.client.get(f"/v1/sessions/{sid}/evidence").json()
    m = b["manifest"]
    assert m["code"]["git_commit"] == "abc123" and m["boundary"] == BOUNDARY_STATEMENT
    assert set(m["inputs"]["devices_sha256"]) == {"sim-display", "sim-audio", "sim-sensor"}
    assert m["code"]["source_tree_sha256"] == evidence.source_tree_sha256()

    def tagged(node):
        if isinstance(node, dict):
            if "value" in node and isinstance(node["value"], (int, float)) and not isinstance(node["value"], bool):
                assert node["tag"] in {"SYNTHETIC", "REPORTED", "TARGET", "UNRUN"}
            for v in node.values():
                tagged(v)

    tagged(b["report"])
    assert b["report"]["claims_made"] == []
    md = world.client.get(f"/v1/sessions/{sid}/evidence", params={"format": "markdown"})
    assert md.headers["content-type"].startswith("text/markdown") and BOUNDARY_STATEMENT in md.text
    assert world.client.get(f"/v1/sessions/{sid}/evidence", params={"format": "pdf"}).status_code == 422
    refused = world.session(devices=["sim-sensor"])
    world.arm(refused)
    md = world.client.get(f"/v1/sessions/{refused}/evidence", params={"format": "markdown"}).text
    assert "Envelope violations" in md and "MISSING_DEVICE" in md


def test_audit_chain_detects_tampering():
    log = AuditLog()
    for i in range(3):
        log.append(i, "x", n=i)
    assert log.verify()
    log.entries[1]["n"] = 99
    assert not log.verify()
    assert log.for_subject("n", 0)


def test_demo_reproduces_committed_outputs(tmp_path):
    summary = demo.run_demo(tmp_path)
    assert summary["completed"]["state"] == "COMPLETED"
    assert summary["latched"]["state"] == "LATCHED" and summary["latched"]["latch_reason"] == "residual_latch"
    assert summary["refused"]["status"] == 422 and summary["refused"]["stored"] is False
    assert summary["audit_chain_ok"] is True
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted(p.name for p in DEMO_DIR.iterdir())
    for name in names:
        assert (tmp_path / name).read_bytes() == (DEMO_DIR / name).read_bytes(), name


def test_cli_demo(tmp_path, capsys):
    assert cli.main(["demo", "--out", str(tmp_path / "d")]) == 0
    assert '"seed": 42' in capsys.readouterr().out


def test_clocks_and_ids():
    c = FakeClock(5)
    assert c.advance(5) == 10
    with pytest.raises(ValueError):
        c.advance(-1)
    m = MonotonicClock()
    assert m.now_ms() <= m.now_ms()
    a, b = IdFactory(1), IdFactory(1)
    assert a.new("p") == b.new("p")
    assert IdFactory().new("p") != IdFactory().new("p")


def test_background_watchdog_stops_without_requests():
    clock = FakeClock(0)
    store = Store(clock=clock, ids=IdFactory(7))
    with TestClient(create_app(store)) as client:
        w = World(client, store, clock, "", [])
        _setup(client)
        w.pid = next(iter(store.participants))
        sid = w.running(devices=["sim-display", "sim-audio"])
        clock.advance(600)
        deadline = time.monotonic() + 3
        while store.sessions[sid].state == State.RUNNING and time.monotonic() < deadline:
            time.sleep(0.02)
        assert store.sessions[sid].state == State.STOPPED


def test_watchdog_fault_forces_stop(world, monkeypatch):
    sid = world.running()

    def boom(*_a, **_k):
        raise RuntimeError("fault")

    monkeypatch.setattr(world.store, "tick", boom)
    world.store.safe_tick()
    assert world.store.sessions[sid].state == State.STOPPED
    assert world.store.sessions[sid].end_reason == "watchdog_fault"


def _setup(client):
    cal = {"date": "2026-10-18", "instrument": "SIMULATED", "tag": "SYNTHETIC"}
    pid = client.post("/v1/participants", json={}).json()["participant_id"]
    client.put(f"/v1/participants/{pid}/preferences", json=demo.PREFERENCES)
    client.post(f"/v1/participants/{pid}/screenings", json=demo.SCREENING)
    client.post(f"/v1/participants/{pid}/consents", json={"consent_version": "0.1", "plain_language_text_sha256":
                                                           "c" * 64, "agreed": True, "input_used": "switch"})
    client.post("/v1/devices", json={"device_id": "sim-display", "kind": "display", "can_actuate": True,
                                     "simulated": True, "limits": {"max_brightness": 0.6}, "calibration": cal})
    client.post("/v1/devices", json={"device_id": "sim-audio", "kind": "audio", "can_actuate": True,
                                     "simulated": True, "limits": {"max_level_dba": 60.0}, "calibration": cal})
    client.post("/v1/protocols", json=demo.CALM_PROTOCOL)


