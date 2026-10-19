# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Seed-42 SYNTHETIC demo, driven through the real HTTP and WebSocket API with a fake clock.

Three outcomes, all simulated (no hardware, no participant):

1. ``fade-calm-01`` (slow visual fade + soft tone) runs to COMPLETED with heartbeats every 200 ms
   and 1 Hz synthetic sensor frames close to the reference.
2. The same protocol LATCHED when a synthetic frame drifts far from the reference (S >= tau).
3. A 7.5 Hz saturated-red flicker over 40% of the field is REFUSED by the envelope and never stored.

The outputs are byte-for-byte reproducible; CI regenerates them and compares.
"""

from __future__ import annotations

import hashlib
import json
import random
import warnings
from pathlib import Path
from typing import Any

with warnings.catch_warnings():  # starlette may warn that TestClient prefers a newer httpx package
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from .app import create_app
from .clock import FakeClock
from .store import IdFactory, Store

SEED = 42
HEARTBEAT_MS = 200
FRAME_MS = 1000
CHANNEL = "oes-0"
CONSENT_TEXT = (
    "Plain-language consent v0.1 (SYNTHETIC demo text). You can stop at any time with any input. "
    "This is not a medical device and nothing is claimed about your health."
)

PREFERENCES = {"input_methods": ["switch", "gaze"], "max_brightness": 0.6, "sound_off": False, "captions": True,
               "pace": "slow"}
SCREENING = {"questionnaire_version": "0.1", "photosensitive_seizure_history": "no",
             "light_triggered_migraine": "no", "discomfort_with_flashing_or_patterns": "no",
             "sound_sensitivity": "no"}
CALM_PROTOCOL = {
    "protocol_id": "fade-calm-01",
    "version": "0.1.0",
    "mode": "wellness_demo",
    "steps": [
        {"modality": "visual", "pattern": "fade", "period_s": 8.0, "brightness_max": 0.5, "colour": "#4060a0",
         "field_fraction": 0.2, "duration_s": 60.0},
        {"modality": "audio", "pattern": "tone", "freq_hz": 220.0, "level_dba": 45.0, "fade_in_s": 5.0,
         "fade_out_s": 5.0, "duration_s": 60.0},
    ],
    "claims": [],
    "evidence_tag": "SYNTHETIC",
}
REFUSED_PROTOCOL = {
    "protocol_id": "flicker-red-7p5",
    "version": "0.1.0",
    "mode": "wellness_demo",
    "steps": [
        {"modality": "visual", "pattern": "flicker", "rate_hz": 7.5, "brightness_max": 1.0, "colour": "#ff0000",
         "field_fraction": 0.4, "duration_s": 600.0},
    ],
    "claims": [{"text": "7.83 Hz flicker entrains brain coherence", "source": "none", "tag": "UNRUN"}],
    "evidence_tag": "UNRUN",
}


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    if response.status_code != status:  # pragma: no cover - demo invariant
        raise RuntimeError(f"unexpected {response.status_code}: {response.text}")
    return response.json()


def build_world(seed: int = SEED) -> tuple[TestClient, Store, FakeClock, dict[str, Any]]:
    clock = FakeClock(0)
    store = Store(clock=clock, ids=IdFactory(seed))
    client = TestClient(create_app(store, watchdog=False))
    rng = random.Random(seed)
    reference = [round(rng.uniform(-1.0, 1.0), 6) for _ in range(32)]
    calibration = {"date": "2026-10-18", "instrument": "SIMULATED", "tag": "SYNTHETIC"}
    pid = _ok(client.post("/v1/participants", json={"label": "demo-01"}), 201)["participant_id"]
    _ok(client.put(f"/v1/participants/{pid}/preferences", json=PREFERENCES))
    _ok(client.post(f"/v1/participants/{pid}/screenings", json=SCREENING), 201)
    text_sha = hashlib.sha256(CONSENT_TEXT.encode()).hexdigest()
    consent = {"consent_version": "0.1", "plain_language_text_sha256": text_sha, "agreed": True, "input_used": "switch"}
    _ok(client.post(f"/v1/participants/{pid}/consents", json=consent), 201)
    devices = [
        {"device_id": "sim-display", "kind": "display", "can_actuate": True, "simulated": True,
         "limits": {"max_flash_hz": 0.0, "max_brightness": 0.6}, "calibration": calibration},
        {"device_id": "sim-audio", "kind": "audio", "can_actuate": True, "simulated": True,
         "limits": {"max_level_dba": 60.0}, "calibration": calibration},
        {"device_id": "sim-sensor", "kind": "sensor", "can_actuate": False, "simulated": True, "limits": {},
         "channels": [CHANNEL], "calibration": {**calibration, "reference": {CHANNEL: reference}}},
    ]
    for d in devices:
        _ok(client.post("/v1/devices", json=d), 201)
    _ok(client.post("/v1/protocols", json=CALM_PROTOCOL), 201)
    return client, store, clock, {"pid": pid, "reference": reference, "rng": rng}


def _frame(sid: str, t_ms: int, x: list[float]) -> dict[str, Any]:
    return {"sid": sid, "t_ms": t_ms, "channel": CHANNEL, "x": x, "tag": "SYNTHETIC"}


def run_session(client: TestClient, clock: FakeClock, ctx: dict[str, Any], *, drift_at_ms: int | None,
                seed: int = SEED) -> dict[str, Any]:
    """Run fade-calm-01 with WebSocket heartbeats; optionally inject a far-off frame at ``drift_at_ms``."""
    body = {"participant_id": ctx["pid"], "protocol_id": "fade-calm-01",
            "device_ids": ["sim-display", "sim-audio", "sim-sensor"], "seed": seed}
    sid = _ok(client.post("/v1/sessions", json=body), 201)["sid"]
    _ok(client.post(f"/v1/sessions/{sid}:arm"))
    _ok(client.post(f"/v1/sessions/{sid}:start", json={"confirmation": {"input": "switch", "intent": "start"}}))
    ref, rng = ctx["reference"], ctx["rng"]
    responses: list[dict[str, Any]] = []
    with client.websocket_connect(f"/v1/sessions/{sid}/stream") as ws:
        ws.receive_json()  # snapshot
        t = 0
        while True:
            clock.advance(HEARTBEAT_MS)
            t += HEARTBEAT_MS
            ws.send_json({"type": "heartbeat"})
            while (msg := ws.receive_json())["type"] != "heartbeat_ack":
                pass
            if msg["state"] != "RUNNING":
                break
            if t % FRAME_MS == 0:
                if drift_at_ms is not None and t >= drift_at_ms:
                    x = [v + 0.8 for v in ref]
                else:
                    x = [round(v + rng.uniform(-0.02, 0.02), 6) for v in ref]
                r = client.post(f"/v1/sessions/{sid}/telemetry", json=_frame(sid, t, x))
                responses.append({"t_ms": t, "status": r.status_code, "action": r.json().get("action", "latch")})
                if r.status_code != 200:
                    break
        ws.send_json({"type": "stop"})
        while ws.receive_json()["type"] != "stop_ack":
            pass
    return {"sid": sid, "session": _ok(client.get(f"/v1/sessions/{sid}")), "frames": responses}


def run_demo(outdir: Path, seed: int = SEED) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    client, store, clock, ctx = build_world(seed)
    completed = run_session(client, clock, ctx, drift_at_ms=None, seed=seed)
    latched = run_session(client, clock, ctx, drift_at_ms=5000, seed=seed)
    cleared = _ok(client.post(f"/v1/sessions/{latched['sid']}:clear-latch",
                              json={"operator_id": "op-demo", "note": "SYNTHETIC demo: drift injected on purpose"}))
    dry_run = _ok(client.post("/v1/protocols/flicker-red-7p5:validate", json=REFUSED_PROTOCOL))
    refused = client.post("/v1/protocols", json=REFUSED_PROTOCOL)
    files: dict[str, str] = {
        "protocol-fade-calm-01.json": _dump(CALM_PROTOCOL),
        "protocol-flicker-red-7p5.json": _dump(REFUSED_PROTOCOL),
        "refused-validate.json": _dump(dry_run),
        "refused-problem.json": _dump({"status": refused.status_code,
                                       "content_type": refused.headers["content-type"], "body": refused.json()}),
    }
    for name, run in (("completed", completed), ("latched", latched)):
        bundle = _ok(client.get(f"/v1/sessions/{run['sid']}/evidence"))
        files[f"session-{name}-evidence.json"] = _dump(bundle)
        files[f"session-{name}-report.md"] = client.get(f"/v1/sessions/{run['sid']}/evidence",
                                                        params={"format": "markdown"}).text
    summary = {
        "boundary": client.get("/v1").json()["boundary"],
        "seed": seed,
        "tag": "SYNTHETIC",
        "completed": {"sid": completed["sid"], "state": completed["session"]["state"],
                      "end_reason": completed["session"]["end_reason"], "frames": len(completed["frames"]),
                      "dimmed": sum(f["action"] == "dim" for f in completed["frames"])},
        "latched": {"sid": latched["sid"], "state": latched["session"]["state"],
                    "latch_reason": latched["session"]["latch"]["reason"],
                    "latch_S": latched["session"]["latch"]["S"], "latch_t_ms": latched["session"]["latch"]["t_ms"],
                    "cleared_by": cleared["latch_cleared"]["operator_id"]},
        "refused": {"protocol_id": "flicker-red-7p5", "status": refused.status_code,
                    "rules": sorted({v["rule"] for v in dry_run["violations"]}),
                    "stored": "flicker-red-7p5" in store.protocols},
        "audit_entries": len(store.audit.entries),
        "audit_chain_ok": store.audit.verify(),
    }
    files["summary.json"] = _dump(summary)
    for name, text in files.items():
        (outdir / name).write_text(text, encoding="utf-8", newline="\n")
    return summary
