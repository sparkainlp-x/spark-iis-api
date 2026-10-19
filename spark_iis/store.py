# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Domain core: participants, devices, protocols and the session state machine (in memory, local only).

State machine (spec section 3)::

    CREATED -> SCREENED -> CONSENTED -> ARMED -> RUNNING -> COMPLETED
                                          |         |-> STOPPED   (participant/operator/heartbeat/consent stop)
                                          |         |-> LATCHED   (safety latch fired)
                                          '-> REFUSED (validation failed; also from CREATED/SCREENED/CONSENTED)

STOPPED, LATCHED, COMPLETED and REFUSED are terminal: no auto-resume. ``stop`` is accepted in every state
and never raises. Output is SIMULATED: levels are computed, never sent to hardware.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import envelope
from .audit import AuditLog, sha256
from .clock import MonotonicClock
from .envelope import Violation
from .latch import DEFAULT_TAU, TAGS, FrameResult, evaluate_frame
from .models import (
    AudioStep,
    Calibration,
    ConsentCreate,
    DeviceDescriptor,
    HapticStep,
    Interaction,
    ParticipantCreate,
    Preferences,
    Protocol,
    Screening,
    SessionCreate,
    StartRequest,
    VisualStep,
)
from .problems import ProblemError, conflict, envelope_refusal, not_found

HEARTBEAT_TIMEOUT_MS = 500
SENSOR_TIMEOUT_MS = 1000  # TARGET: a sensor silent this long while RUNNING counts as missing
COMFORT_STOP_AT_OR_BELOW = 2


class State(str, Enum):
    CREATED = "CREATED"
    SCREENED = "SCREENED"
    CONSENTED = "CONSENTED"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    LATCHED = "LATCHED"
    REFUSED = "REFUSED"


TERMINAL = frozenset({State.COMPLETED, State.STOPPED, State.LATCHED, State.REFUSED})
TRANSITIONS: dict[State, frozenset[State]] = {
    State.CREATED: frozenset({State.SCREENED, State.REFUSED, State.STOPPED}),
    State.SCREENED: frozenset({State.CONSENTED, State.REFUSED, State.STOPPED}),
    State.CONSENTED: frozenset({State.ARMED, State.REFUSED, State.STOPPED}),
    State.ARMED: frozenset({State.RUNNING, State.REFUSED, State.STOPPED}),
    State.RUNNING: frozenset({State.COMPLETED, State.STOPPED, State.LATCHED}),
    **{s: frozenset() for s in TERMINAL},
}
ZERO_OUTPUT = {"visual_brightness": 0.0, "audio_level_dba": 0.0, "haptic_amplitude": 0.0}


def _frame_tag(frame: Any) -> str:
    tag = frame.get("tag") if isinstance(frame, dict) else None
    return tag if isinstance(tag, str) and tag in TAGS else "INVALID"


class IdFactory:
    """Random pseudonymous IDs, or deterministic ones for seeded synthetic demos."""

    def __init__(self, seed: int | None = None) -> None:
        self.seed, self.counter = seed, 0

    def new(self, prefix: str) -> str:
        if self.seed is None:
            return f"{prefix}-{secrets.token_hex(6)}"
        self.counter += 1
        return f"{prefix}-{hashlib.sha256(f'{self.seed}:{prefix}:{self.counter}'.encode()).hexdigest()[:12]}"


@dataclass
class Participant:
    pid: str
    label: str
    preferences: Preferences | None = None
    screenings: list[Screening] = field(default_factory=list)
    consents: list[dict[str, Any]] = field(default_factory=list)
    runs: list[tuple[int, int]] = field(default_factory=list)  # (start_ms, end_ms) of RUNNING periods
    uncleared_latch: str | None = None

    def active_consent(self) -> dict[str, Any] | None:
        live = [c for c in self.consents if not c["withdrawn"]]
        return live[-1] if live else None


@dataclass
class Session:
    sid: str
    participant_id: str
    protocol: Protocol
    device_ids: list[str]
    seed: int | None
    created_ms: int
    state: State = State.CREATED
    tau: float = DEFAULT_TAU
    transitions: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)
    device_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    device_hashes: dict[str, str] = field(default_factory=dict)
    references: dict[str, list[float]] = field(default_factory=dict)
    start_ms: int | None = None
    end_ms: int | None = None
    end_reason: str | None = None
    last_heartbeat_ms: int | None = None
    last_frame_ms: dict[str, int] = field(default_factory=dict)
    last_t_ms: dict[str, int] = field(default_factory=dict)
    dim_factor: float = 1.0
    telemetry: list[dict[str, Any]] = field(default_factory=list)
    telemetry_hashes: list[str] = field(default_factory=list)
    telemetry_deleted: bool = False
    latch: dict[str, Any] | None = None
    latch_cleared: dict[str, Any] | None = None
    frame_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return round(envelope.protocol_duration_s(self.protocol) * 1000)

    def elapsed_ms(self, now: int) -> int:
        if self.start_ms is None:
            return 0
        return (self.end_ms if self.end_ms is not None else now) - self.start_ms


def _step_level(step: VisualStep | AudioStep | HapticStep, t_s: float) -> float:
    """Nominal (pre-dimming) level of one step at protocol time ``t_s``; 0 outside the step."""
    local = t_s - step.start_s
    if local < 0 or local >= step.duration_s:
        return 0.0
    if isinstance(step, VisualStep):
        if step.pattern == "fade" and step.period_s:
            return step.brightness_max * (0.5 - 0.5 * math.cos(2.0 * math.pi * local / step.period_s))
        if step.pattern == "static":
            return step.brightness_max
        return 0.0  # flicker/flash never validate, so never render
    if isinstance(step, AudioStep):
        ramp = min(1.0, local / step.fade_in_s if step.fade_in_s else 1.0,
                   (step.duration_s - local) / step.fade_out_s if step.fade_out_s else 1.0)
        return step.level_dba * max(0.0, ramp)
    if step.pattern == "pulse" and step.pulse_on_s and step.pulse_off_s:
        return step.amplitude if local % (step.pulse_on_s + step.pulse_off_s) < step.pulse_on_s else 0.0
    return step.amplitude


def nominal_output(protocol: Protocol, t_s: float) -> dict[str, float]:
    out = dict(ZERO_OUTPUT)
    for step in protocol.steps:
        key = {"visual": "visual_brightness", "audio": "audio_level_dba", "haptic": "haptic_amplitude"}[step.modality]
        out[key] = max(out[key], _step_level(step, t_s))
    return out


class Store:
    def __init__(self, clock: Any | None = None, ids: IdFactory | None = None) -> None:
        self.clock = clock or MonotonicClock()
        self.ids = ids or IdFactory()
        self.lock = threading.RLock()
        self.participants: dict[str, Participant] = {}
        self.devices: dict[str, DeviceDescriptor] = {}
        self.calibration_history: dict[str, list[dict[str, Any]]] = {}
        self.protocols: dict[str, Protocol] = {}
        self.sessions: dict[str, Session] = {}
        self.audit = AuditLog()

    # ------------------------------------------------------------------ helpers
    def now(self) -> int:
        return self.clock.now_ms()

    def participant(self, pid: str) -> Participant:
        if pid not in self.participants:
            raise not_found(f"participant {pid}")
        return self.participants[pid]

    def session(self, sid: str) -> Session:
        if sid not in self.sessions:
            raise not_found(f"session {sid}")
        return self.sessions[sid]

    def _event(self, s: Session, t_ms: int, kind: str, **fields: Any) -> dict[str, Any]:
        event = {"seq": len(s.events), "t_ms": t_ms - s.created_ms, "type": kind, **fields}
        s.events.append(event)
        return event

    def _transition(self, s: Session, to: State, t_ms: int, reason: str) -> None:
        if to not in TRANSITIONS[s.state]:  # pragma: no cover - guarded by callers; a bug if reached
            raise AssertionError(f"illegal transition {s.state} -> {to}")
        s.transitions.append({"t_ms": t_ms - s.created_ms, "from": s.state.value, "to": to.value, "reason": reason})
        self._event(s, t_ms, "state", state=to.value, previous=s.state.value, reason=reason)
        self.audit.append(t_ms, "session_state", sid=s.sid, state=to.value, reason=reason)
        if s.state == State.RUNNING and to in TERMINAL:
            s.end_ms, s.end_reason = t_ms, reason
            self.participants[s.participant_id].runs.append((s.start_ms if s.start_ms is not None else t_ms, t_ms))
        elif to in TERMINAL:
            s.end_ms, s.end_reason = t_ms, reason
        s.state = to

    # ------------------------------------------------------------------ participants
    def create_participant(self, body: ParticipantCreate) -> Participant:
        with self.lock:
            p = Participant(self.ids.new("p"), body.label)
            self.participants[p.pid] = p
            self.audit.append(self.now(), "participant_created", pid=p.pid)
            return p

    def set_preferences(self, pid: str, prefs: Preferences) -> Participant:
        with self.lock:
            p = self.participant(pid)
            p.preferences = prefs
            self.audit.append(self.now(), "preferences_set", pid=pid, sha256=sha256(prefs.model_dump()))
            return p

    def add_screening(self, pid: str, screening: Screening) -> dict[str, Any]:
        with self.lock:
            p = self.participant(pid)
            p.screenings.append(screening)
            blocked = envelope.blocks_visual_modulation(screening)
            self.audit.append(self.now(), "screening", pid=pid, visual_modulation_blocked=blocked)
            return {"participant_id": pid, "visual_modulation_blocked": blocked, "screening_count": len(p.screenings)}

    def add_consent(self, pid: str, consent: ConsentCreate) -> dict[str, Any]:
        with self.lock:
            p = self.participant(pid)
            record = {"consent_id": self.ids.new("c"), **consent.model_dump(), "withdrawn": False}
            p.consents.append(record)
            self.audit.append(self.now(), "consent_given", pid=pid, consent_id=record["consent_id"],
                              consent_version=consent.consent_version)
            return record

    def withdraw_consent(self, pid: str, consent_id: str) -> dict[str, Any]:
        """Withdraw consent: stop the participant's sessions and delete raw telemetry, keeping only hashes."""
        with self.lock:
            p = self.participant(pid)
            record = next((c for c in p.consents if c["consent_id"] == consent_id), None)
            if record is None:
                raise not_found(f"consent {consent_id}")
            now = self.now()
            record["withdrawn"] = True
            deleted = 0
            for s in self.sessions.values():
                if s.participant_id != pid:
                    continue
                if s.state not in TERMINAL:
                    self.stop(s.sid, reason="consent_withdrawn")
                deleted += len(s.telemetry)
                s.telemetry.clear()
                s.telemetry_deleted = True
            self.audit.append(now, "consent_withdrawn", pid=pid, consent_id=consent_id, frames_deleted=deleted)
            return {"consent_id": consent_id, "withdrawn": True, "raw_frames_deleted": deleted,
                    "hashes_kept": True}

    # ------------------------------------------------------------------ devices and protocols
    def register_device(self, device: DeviceDescriptor) -> DeviceDescriptor:
        with self.lock:
            problems = envelope.check_device(device)
            if problems:
                raise envelope_refusal("device descriptor refused", [v.to_dict() for v in problems])
            if device.device_id in self.devices:
                raise ProblemError(409, "CONFLICT", "Already exists", f"device {device.device_id} already exists")
            self.devices[device.device_id] = device
            self.calibration_history[device.device_id] = []
            if device.calibration is not None:
                self.calibration_history[device.device_id].append(device.calibration.model_dump())
            self.audit.append(self.now(), "device_registered", device_id=device.device_id,
                              sha256=sha256(device.model_dump()))
            return device

    def add_calibration(self, device_id: str, calibration: Calibration) -> DeviceDescriptor:
        with self.lock:
            if device_id not in self.devices:
                raise not_found(f"device {device_id}")
            device = self.devices[device_id]
            problems = envelope.check_calibration(device, calibration)
            if problems:
                raise envelope_refusal("calibration refused", [v.to_dict() for v in problems])
            updated = device.model_copy(update={"calibration": calibration})
            self.devices[device_id] = updated
            self.calibration_history[device_id].append(calibration.model_dump())
            self.audit.append(self.now(), "calibration", device_id=device_id, tag=calibration.tag,
                              sha256=sha256(calibration.model_dump()))
            return updated

    def submit_protocol(self, protocol: Protocol) -> Protocol:
        with self.lock:
            problems = envelope.check_protocol(protocol)
            if problems:
                raise envelope_refusal("protocol refused; it was not stored", [v.to_dict() for v in problems])
            if protocol.protocol_id in self.protocols:
                raise ProblemError(409, "CONFLICT", "Already exists", f"protocol {protocol.protocol_id} exists")
            self.protocols[protocol.protocol_id] = protocol
            self.audit.append(self.now(), "protocol_stored", protocol_id=protocol.protocol_id,
                              sha256=sha256(protocol.model_dump()))
            return protocol

    # ------------------------------------------------------------------ sessions
    def create_session(self, body: SessionCreate) -> Session:
        with self.lock:
            self.participant(body.participant_id)
            if body.protocol_id not in self.protocols:
                raise not_found(f"protocol {body.protocol_id}")
            for d in body.device_ids:
                if d not in self.devices:
                    raise not_found(f"device {d}")
            if len(set(body.device_ids)) != len(body.device_ids):
                raise envelope_refusal("duplicate device", [Violation("DUPLICATE_DEVICE", "device_ids", "duplicate")
                                                            .to_dict()])
            now = self.now()
            s = Session(self.ids.new("s"), body.participant_id, self.protocols[body.protocol_id],
                        list(body.device_ids), body.seed, now)
            self.sessions[s.sid] = s
            self._event(s, now, "state", state=s.state.value, previous=None, reason="created")
            self.audit.append(now, "session_created", sid=s.sid, pid=s.participant_id,
                              protocol_id=body.protocol_id)
            return s

    def _refuse(self, s: Session, now: int, violations: Sequence[Violation]) -> ProblemError:
        s.violations = [v.to_dict() for v in violations]
        self._transition(s, State.REFUSED, now, violations[0].rule)
        return envelope_refusal("session refused at ARM", s.violations, state=s.state.value, sid=s.sid)

    def _break_violation(self, p: Participant, s: Session, now: int) -> Violation | None:
        """20 min (TARGET) of RUNNING time, then a mandatory 10 min break."""
        block = 0
        prev_end = None
        for start, end in p.runs:
            if prev_end is not None and start - prev_end >= envelope.MANDATORY_BREAK_S * 1000:
                block = 0
            block += end - start
            prev_end = end
        if prev_end is not None and now - prev_end >= envelope.MANDATORY_BREAK_S * 1000:
            block = 0
        if block + s.duration_ms > envelope.MAX_SESSION_S * 1000:
            return Violation("BREAK_REQUIRED", "participant", "a 10 min break is required after 20 min (TARGET)",
                             block / 1000.0, envelope.MAX_SESSION_S)
        return None

    def arm(self, sid: str) -> Session:
        with self.lock:
            s = self.session(sid)
            self.tick(sid)
            if s.state != State.CREATED:
                raise conflict(f"ARM needs CREATED; session is {s.state.value}", state=s.state.value)
            now = self.now()
            p = self.participants[s.participant_id]
            if not p.screenings:
                raise self._refuse(s, now, [Violation("SCREENING_MISSING", "participant", "no screening on file")])
            self._transition(s, State.SCREENED, now, "screening_on_file")
            if p.active_consent() is None:
                raise self._refuse(s, now, [Violation("CONSENT_MISSING", "participant", "no active consent")])
            self._transition(s, State.CONSENTED, now, "consent_active")
            devices = [self.devices[d] for d in s.device_ids]
            violations: list[Violation] = []
            if p.preferences is None:
                violations.append(Violation("PREFERENCES_MISSING", "participant", "no accessibility profile"))
            if p.uncleared_latch is not None:
                violations.append(Violation("LATCH_NOT_CLEARED", "participant",
                                            "an operator must clear the previous latch", p.uncleared_latch))
            busy = [o.sid for o in self.sessions.values()
                    if o.participant_id == p.pid and o.sid != s.sid and o.state in (State.ARMED, State.RUNNING)]
            if busy:
                violations.append(Violation("CONCURRENT_SESSION", "participant", "another session is active", busy))
            brk = self._break_violation(p, s, now)
            if brk:
                violations.append(brk)
            violations += envelope.check_protocol(s.protocol, screening=p.screenings[-1], preferences=p.preferences,
                                                  devices=devices)
            if violations:
                raise self._refuse(s, now, violations)
            s.device_snapshot = {d.device_id: d.model_dump() for d in devices}
            s.device_hashes = {d.device_id: sha256(d.model_dump()) for d in devices}
            s.references = {ch: list(ref) for d in devices if d.kind == "sensor" and d.calibration
                            for ch, ref in d.calibration.reference.items() if ch in d.channels}
            self._transition(s, State.ARMED, now, "envelope_ok")
            return s

    def start(self, sid: str, body: StartRequest) -> Session:
        with self.lock:
            s = self.session(sid)
            self.tick(sid)
            if s.state != State.ARMED:
                raise conflict(f"START needs ARMED; session is {s.state.value}", state=s.state.value)
            now = self.now()
            p = self.participants[s.participant_id]
            if p.preferences is None or body.confirmation.input not in p.preferences.input_methods:
                raise envelope_refusal("confirmation must come from one of the participant's input methods",
                                       [Violation("CONFIRMATION_INPUT", "confirmation.input",
                                                  "input not in the accessibility profile",
                                                  body.confirmation.input).to_dict()])
            if self._references_stale(s):
                raise self._refuse(s, now, [Violation("STALE_CALIBRATION", "devices",
                                                      "a device changed after ARM; ARM a new session")])
            self._event(s, now, "confirmation", input=body.confirmation.input, intent="start")
            s.start_ms = s.last_heartbeat_ms = now
            s.last_frame_ms = {ch: now for ch in s.references}
            self._transition(s, State.RUNNING, now, "participant_confirmed")
            return s

    def _references_stale(self, s: Session) -> bool:
        return any(sha256(self.devices[d].model_dump()) != h for d, h in s.device_hashes.items())

    def stop(self, sid: str, reason: str = "stop_requested") -> dict[str, Any]:
        """Always accepted, idempotent, never raises. Output goes to zero immediately (within the 1 s TARGET)."""
        try:
            with self.lock:
                s = self.sessions.get(sid)
                if s is None:
                    return {"sid": sid, "known": False, "state": None, "stopped": True, "output": dict(ZERO_OUTPUT)}
                self.tick(sid)
                already = s.state in TERMINAL
                if not already:
                    self._transition(s, State.STOPPED, self.now(), reason)
                else:
                    self._event(s, self.now(), "stop_repeated", reason=reason, state=s.state.value)
                return {"sid": sid, "known": True, "state": s.state.value, "stopped": True,
                        "already_terminal": already, "output": dict(ZERO_OUTPUT)}
        except Exception:  # noqa: BLE001 - stop must never fail; force the safe state
            s = self.sessions.get(sid)
            if s is not None:
                s.state = State.STOPPED if s.state not in TERMINAL else s.state
                s.end_reason = s.end_reason or "stop_forced"
            return {"sid": sid, "known": s is not None, "state": s.state.value if s else None, "stopped": True,
                    "forced": True, "output": dict(ZERO_OUTPUT)}

    def heartbeat(self, sid: str) -> dict[str, Any]:
        with self.lock:
            s = self.session(sid)
            self.tick(sid)
            if s.state == State.RUNNING:
                s.last_heartbeat_ms = self.now()
            return {"type": "heartbeat_ack", "state": s.state.value, "t_ms": self.now() - s.created_ms}

    def interaction(self, sid: str, body: Interaction) -> dict[str, Any]:
        with self.lock:
            s = self.session(sid)
            self.tick(sid)
            now = self.now()
            if body.intent == "stop":  # any input, any state
                self._event(s, now, "interaction", input=body.input, intent="stop")
                return self.stop(sid, reason="participant_stop")
            p = self.participants[s.participant_id]
            if p.preferences is None or body.input not in p.preferences.input_methods:
                raise envelope_refusal("input not in the participant's accessibility profile",
                                       [Violation("INPUT_NOT_IN_PROFILE", "input", "unknown input", body.input)
                                        .to_dict()])
            if body.intent == "comfort_rating":
                if body.comfort_rating is None:
                    raise envelope_refusal("comfort_rating needs a value 1-5",
                                           [Violation("MISSING_VALUE", "comfort_rating", "missing").to_dict()])
            elif body.comfort_rating is not None:
                raise envelope_refusal("comfort_rating only with intent comfort_rating",
                                       [Violation("UNEXPECTED_FIELD", "comfort_rating", "unexpected").to_dict()])
            if s.state != State.RUNNING:
                raise conflict(f"interaction '{body.intent}' needs RUNNING; session is {s.state.value}",
                               state=s.state.value)
            self._event(s, now, "interaction", input=body.input, intent=body.intent, comfort_rating=body.comfort_rating)
            if body.intent == "comfort_rating" and body.comfort_rating <= COMFORT_STOP_AT_OR_BELOW:
                return self.stop(sid, reason="comfort_rating_low")
            if body.intent == "dim":
                s.dim_factor = s.dim_factor * 0.5
            return {"sid": sid, "state": s.state.value, "dim_factor": s.dim_factor}

    def telemetry(self, sid: str, raw_bytes: bytes, frame: Any, parse_error: str | None) -> FrameResult:
        with self.lock:
            s = self.session(sid)
            self.tick(sid)
            now = self.now()
            if s.state != State.RUNNING:
                raise conflict(f"telemetry needs RUNNING; session is {s.state.value}", state=s.state.value)
            digest = hashlib.sha256(raw_bytes).hexdigest()
            if parse_error is not None:
                result = FrameResult("latch", "invalid_frame", parse_error)
            else:
                try:
                    result = evaluate_frame(frame, sid=sid, channels=s.references, last_t_ms=s.last_t_ms,
                                            reference_fresh=not self._references_stale(s), tau=s.tau)
                except Exception:  # noqa: BLE001 - a frame that breaks the evaluator latches (fail closed)
                    result = FrameResult("latch", "invalid_frame", "frame could not be evaluated")
            s.telemetry_hashes.append(digest)
            s.telemetry.append({"sha256": digest, "raw": raw_bytes.decode("utf-8", "replace")})
            if result.channel is not None and result.t_ms is not None and result.reason != "invalid_frame":
                s.last_t_ms[result.channel] = result.t_ms
                s.last_frame_ms[result.channel] = now
            elif result.channel is not None and result.reason == "invalid_frame":
                s.last_frame_ms[result.channel] = now
            record = {"t_ms": now - s.created_ms, "sha256": digest, "action": result.action, "reason": result.reason,
                      "channel": result.channel, "R": result.R, "S": result.S,
                      "tag": _frame_tag(frame)}
            s.frame_results.append(record)
            if result.action == "dim":
                s.dim_factor = s.dim_factor * 0.5
                self._event(s, now, "dim", channel=result.channel, S=result.S, dim_factor=s.dim_factor)
            elif result.latched:
                self._latch(s, now, result.reason, result.detail, R=result.R, S=result.S, channel=result.channel)
            return result

    def _latch(self, s: Session, t_ms: int, reason: str, detail: str, **fields: Any) -> None:
        s.latch = {"sid": s.sid, "t_ms": t_ms - s.created_ms, "tau": s.tau, "reason": reason, "detail": detail,
                   "action": "stop", "tag": "SYNTHETIC" if s.seed is not None else "UNRUN", **fields}
        self.participants[s.participant_id].uncleared_latch = s.sid
        self._event(s, t_ms, "latch", latch=dict(s.latch))
        self._transition(s, State.LATCHED, t_ms, reason)

    def clear_latch(self, sid: str, operator_id: str, note: str) -> Session:
        with self.lock:
            s = self.session(sid)
            if s.state != State.LATCHED:
                raise conflict(f"only a LATCHED session can be cleared; session is {s.state.value}")
            if s.latch_cleared is not None:
                raise conflict("latch already cleared", cleared=s.latch_cleared)
            now = self.now()
            s.latch_cleared = {"operator_id": operator_id, "note": note, "t_ms": now - s.created_ms}
            p = self.participants[s.participant_id]
            if p.uncleared_latch == sid:
                p.uncleared_latch = None
            self._event(s, now, "latch_cleared", operator_id=operator_id, note=note)
            self.audit.append(now, "latch_cleared", sid=sid, operator_id=operator_id, note_sha256=sha256(note))
            return s

    # ------------------------------------------------------------------ watchdog
    def force_stop_all(self, reason: str = "watchdog_fault") -> None:
        """Last resort when the watchdog itself fails: every non-terminal session is forced to STOPPED."""
        for s in self.sessions.values():
            if s.state not in TERMINAL:
                s.state, s.end_reason = State.STOPPED, reason
                if s.end_ms is None:
                    s.end_ms = s.start_ms if s.start_ms is not None else s.created_ms

    def safe_tick(self) -> None:
        try:
            self.tick()
        except Exception:  # noqa: BLE001 - fail closed
            self.force_stop_all()

    def tick(self, sid: str | None = None) -> None:
        """Apply heartbeat, missing-sensor and completion deadlines (earliest first) up to now."""
        with self.lock:
            now = self.now()
            targets = [self.sessions[sid]] if sid is not None else list(self.sessions.values())
            for s in targets:
                if s.state != State.RUNNING or s.start_ms is None:
                    continue
                deadlines: list[tuple[int, int, str]] = [
                    (s.start_ms + s.duration_ms, 2, "completed"),
                    (s.start_ms + int(envelope.MAX_SESSION_S * 1000), 3, "session_length"),
                ]
                if s.last_heartbeat_ms is not None:
                    deadlines.append((s.last_heartbeat_ms + HEARTBEAT_TIMEOUT_MS + 1, 0, "heartbeat_missed"))
                for ch, last in s.last_frame_ms.items():
                    deadlines.append((last + SENSOR_TIMEOUT_MS + 1, 1, f"missing_sensor:{ch}"))
                due = min(deadlines)
                if due[0] > now:
                    continue
                at, _, what = due
                if what == "completed":
                    self._transition(s, State.COMPLETED, at, "protocol_complete")
                elif what.startswith("missing_sensor"):
                    self._latch(s, at, "missing_sensor", f"no frame from {what.split(':', 1)[1]} for >1000 ms",
                                channel=what.split(":", 1)[1])
                else:
                    self._transition(s, State.STOPPED, at, what)

    # ------------------------------------------------------------------ views
    def output(self, s: Session, now: int | None = None) -> dict[str, float]:
        now = self.now() if now is None else now
        if s.state != State.RUNNING or s.start_ms is None:
            return dict(ZERO_OUTPUT)
        nominal = nominal_output(s.protocol, (now - s.start_ms) / 1000.0)
        return {k: v * s.dim_factor for k, v in nominal.items()}

    def session_view(self, s: Session) -> dict[str, Any]:
        now = self.now()
        return {
            "sid": s.sid,
            "participant_id": s.participant_id,
            "protocol_id": s.protocol.protocol_id,
            "device_ids": s.device_ids,
            "state": s.state.value,
            "terminal": s.state in TERMINAL,
            "end_reason": s.end_reason,
            "latched": s.latch is not None,
            "latch": s.latch,
            "latch_cleared": s.latch_cleared,
            "elapsed_ms": s.elapsed_ms(now),
            "duration_ms": s.duration_ms,
            "dim_factor": s.dim_factor,
            "output": self.output(s, now),
            "output_is_simulated": True,
            "violations": s.violations,
        }
