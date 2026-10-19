# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Provenance manifest and JSON/Markdown evidence report for one session (spec section 7).

Every number is wrapped as ``{"value": ..., "tag": ...}``. Output levels are always SYNTHETIC because every
device is simulated. Nothing in the report is a measure of wellbeing, coherence or any health outcome.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from . import __version__, envelope
from .audit import canonical, sha256
from .boundary import BOUNDARY_STATEMENT
from .latch import DEFAULT_TAU, DIM_THRESHOLD
from .store import Session, Store

SCHEMA = "spark-iis-evidence/0.1"
PACKAGE_DIR = Path(__file__).resolve().parent
NOT_MEASURED = [
    "any physiological, neurological or health outcome",
    "comfort, wellbeing, relaxation or 'coherence'",
    "real light, sound or vibration output (devices are simulated)",
    "latency on real hardware",
]


def source_tree_sha256() -> str:
    """SHA-256 over the package's Python sources (sorted relative path + content hash), newline-normalised."""
    h = hashlib.sha256()
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        rel = path.relative_to(PACKAGE_DIR).as_posix()
        data = path.read_bytes().replace(b"\r\n", b"\n")
        h.update(f"{rel}\0{hashlib.sha256(data).hexdigest()}\n".encode())
    return h.hexdigest()


def _n(value: Any, tag: str, unit: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value, "tag": tag}
    if unit:
        out["unit"] = unit
    return out


def build_report(store: Store, s: Session) -> dict[str, Any]:
    data_tag = "SYNTHETIC"  # simulated devices; telemetry frames carry their own tags (counted below)
    results = s.frame_results
    scores = [r["S"] for r in results if r["S"] is not None]
    residuals = [r["R"] for r in results if r["R"] is not None]
    frame_tags: dict[str, int] = {}
    for r in results:
        frame_tags[r["tag"]] = frame_tags.get(r["tag"], 0) + 1
    return {
        "schema": SCHEMA,
        "boundary": BOUNDARY_STATEMENT,
        "sid": s.sid,
        "participant_id": s.participant_id,
        "protocol_id": s.protocol.protocol_id,
        "protocol_version": s.protocol.version,
        "protocol_evidence_tag": s.protocol.evidence_tag,
        "seed": s.seed,
        "final_state": s.state.value,
        "end_reason": s.end_reason,
        "transitions": s.transitions,
        "planned_duration_s": _n(s.duration_ms / 1000.0, "TARGET", "s"),
        "elapsed_running_s": _n(s.elapsed_ms(store.now()) / 1000.0, data_tag, "s"),
        "final_dim_factor": _n(s.dim_factor, data_tag),
        "frames": {
            "received": _n(len(s.telemetry_hashes), data_tag),
            "raw_retained": _n(len(s.telemetry), data_tag),
            "raw_deleted_by_consent_withdrawal": s.telemetry_deleted,
            "dimmed": _n(sum(r["action"] == "dim" for r in results), data_tag),
            "latched": _n(sum(r["action"] == "latch" for r in results), data_tag),
            "max_R": _n(max(residuals) if residuals else None, data_tag),
            "max_S": _n(max(scores) if scores else None, data_tag),
            "frame_tags_seen": frame_tags,
        },
        "latch": s.latch,
        "latch_cleared": s.latch_cleared,
        "violations": s.violations,
        "thresholds": {
            "tau": _n(s.tau, "TARGET"),
            "dim_threshold": _n(DIM_THRESHOLD, "TARGET"),
            "default_tau": _n(DEFAULT_TAU, "TARGET"),
        },
        "envelope_limits": envelope.LIMITS,
        "claims_made": [],
        "not_measured": NOT_MEASURED,
    }


def render_markdown(report: dict[str, Any], manifest: dict[str, Any]) -> str:
    f = report["frames"]

    def v(x: dict[str, Any]) -> str:
        unit = f" {x['unit']}" if "unit" in x else ""
        return f"{x['value']}{unit} ({x['tag']})"

    lines = [
        f"# Evidence report: session `{report['sid']}`",
        "",
        f"> {report['boundary']}",
        "",
        "All device output in this report is **simulated**. No hardware was driven.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Protocol | `{report['protocol_id']}` {report['protocol_version']} "
        f"(evidence tag {report['protocol_evidence_tag']}) |",
        f"| Seed | {report['seed']} |",
        f"| Final state | **{report['final_state']}** ({report['end_reason']}) |",
        f"| Planned duration | {v(report['planned_duration_s'])} |",
        f"| Elapsed running time | {v(report['elapsed_running_s'])} |",
        f"| Final dim factor | {v(report['final_dim_factor'])} |",
        f"| Frames received / raw retained | {v(f['received'])} / {v(f['raw_retained'])} |",
        f"| Frames dimmed / latched | {v(f['dimmed'])} / {v(f['latched'])} |",
        f"| Max R / max S | {v(f['max_R'])} / {v(f['max_S'])} |",
        f"| tau / dim threshold | {v(report['thresholds']['tau'])} / {v(report['thresholds']['dim_threshold'])} |",
        "",
        "## Transitions",
        "",
    ]
    lines += [f"- t={t['t_ms']} ms: {t['from']} -> {t['to']} ({t['reason']})" for t in report["transitions"]]
    if report["latch"]:
        lat = report["latch"]
        lines += ["", "## Latch", "", f"- reason: `{lat['reason']}`; detail: {lat['detail']}; action: stop"]
    if report["violations"]:
        lines += ["", "## Envelope violations", ""]
        lines += [f"- `{x['rule']}` at `{x['path']}`: {x['message']}" for x in report["violations"]]
    lines += ["", "## Not measured", ""] + [f"- {x}" for x in report["not_measured"]]
    lines += ["", "## Provenance", "", (f"- code: spark-iis-api {manifest['code']['version']}, "
              f"source tree sha256 `{manifest['code']['source_tree_sha256']}`, "
              f"commit `{manifest['code']['git_commit']}`"),
              f"- protocol sha256 `{manifest['inputs']['protocol_sha256']}`",
              f"- event log sha256 `{manifest['outputs']['events_sha256']}`",
              f"- telemetry hash-list sha256 `{manifest['outputs']['telemetry_hashes_sha256']}`",
              f"- report sha256 `{manifest['outputs']['report_sha256']}`", ""]
    return "\n".join(lines)


def build_evidence(store: Store, s: Session) -> dict[str, Any]:
    with store.lock:
        store.tick(s.sid)
        report = build_report(store, s)
        devices = s.device_snapshot or {d: store.devices[d].model_dump() for d in s.device_ids}
        manifest = {
            "schema": SCHEMA,
            "boundary": BOUNDARY_STATEMENT,
            "sid": s.sid,
            "seed": s.seed,
            "code": {
                "package": "spark-iis-api",
                "version": __version__,
                "source_tree_sha256": source_tree_sha256(),
                "git_commit": os.environ.get("SPARK_IIS_GIT_COMMIT", "unrecorded"),
            },
            "inputs": {
                "protocol_sha256": sha256(s.protocol.model_dump()),
                "devices_sha256": {k: sha256(v) for k, v in sorted(devices.items())},
                "calibrations_sha256": {k: (sha256(v["calibration"]) if v.get("calibration") else None)
                                        for k, v in sorted(devices.items())},
            },
            "outputs": {
                "events_sha256": sha256(s.events),
                "telemetry_hashes_sha256": sha256(s.telemetry_hashes),
                "report_sha256": hashlib.sha256(canonical(report)).hexdigest(),
            },
            "clock": "monotonic milliseconds since session creation; no wall-clock time is recorded",
        }
        return {"manifest": manifest, "report": report, "markdown": render_markdown(report, manifest),
                "events": s.events}
