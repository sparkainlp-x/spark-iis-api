# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI application: every ``/v1`` endpoint of the spec, plus the WebSocket heartbeat stream."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, envelope
from .boundary import BOUNDARY_HEADER, BOUNDARY_STATEMENT
from .evidence import build_evidence
from .models import (
    Calibration,
    ClearLatch,
    ConsentCreate,
    DeviceDescriptor,
    Interaction,
    ParticipantCreate,
    Preferences,
    Protocol,
    Screening,
    SessionCreate,
    StartRequest,
)
from .problems import PROBLEM_MEDIA_TYPE, ProblemError
from .store import TERMINAL, Store

MAX_TELEMETRY_BYTES = 64 * 1024
WATCHDOG_INTERVAL_S = 0.05
WS_RECEIVE_TIMEOUT_S = 0.1


def problem_response(exc: ProblemError, instance: str) -> JSONResponse:
    return JSONResponse(exc.body(instance), status_code=exc.status, media_type=PROBLEM_MEDIA_TYPE)


def create_app(store: Store | None = None, *, watchdog: bool = True) -> FastAPI:
    store = store or Store()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if watchdog:
            async def loop() -> None:
                while True:
                    store.safe_tick()
                    await asyncio.sleep(WATCHDOG_INTERVAL_S)

            task = asyncio.create_task(loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Spark IIS API", version=__version__, description=BOUNDARY_STATEMENT, lifespan=lifespan)
    app.state.store = store

    @app.middleware("http")
    async def boundary_header(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers[BOUNDARY_HEADER] = BOUNDARY_STATEMENT
        return response

    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(exc, request.url.path)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Fail closed: unknown, missing or malformed fields are refused. The input is never echoed back.
        errors = [{"loc": [str(x) for x in e.get("loc", ())], "type": e.get("type"), "msg": e.get("msg")}
                  for e in exc.errors()]
        violations = [{"rule": "FAIL_CLOSED_VALIDATION", "path": ".".join(e["loc"]), "message": e["msg"],
                       "value": None, "limit": None} for e in errors]
        return problem_response(ProblemError(422, "SAFETY_ENVELOPE", "Refused: request failed validation",
                                             "unknown, missing or malformed fields are refused (fail closed)",
                                             rule="FAIL_CLOSED_VALIDATION", violations=violations,
                                             extra={"errors": errors}), request.url.path)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return problem_response(ProblemError(exc.status_code, code, str(exc.detail), str(exc.detail)),
                                request.url.path)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover - defensive
        return problem_response(ProblemError(500, "INTERNAL", "Internal error", "internal error; output is zero"),
                                request.url.path)

    @app.middleware("http")
    async def lazy_watchdog(request: Request, call_next: Any) -> Any:
        store.safe_tick()
        return await call_next(request)

    # ------------------------------------------------------------------ service
    @app.get("/v1")
    def info() -> dict[str, Any]:
        return {"service": "spark-iis-api", "version": __version__, "boundary": BOUNDARY_STATEMENT,
                "devices": "simulated only", "limits": envelope.LIMITS}

    # ------------------------------------------------------------------ participants
    @app.post("/v1/participants", status_code=201)
    def create_participant(body: ParticipantCreate) -> dict[str, Any]:
        p = store.create_participant(body)
        return {"participant_id": p.pid, "label": p.label}

    @app.put("/v1/participants/{pid}/preferences")
    def put_preferences(pid: str, body: Preferences) -> dict[str, Any]:
        p = store.set_preferences(pid, body)
        return {"participant_id": p.pid, "preferences": body.model_dump()}

    @app.post("/v1/participants/{pid}/screenings", status_code=201)
    def post_screening(pid: str, body: Screening) -> dict[str, Any]:
        return store.add_screening(pid, body)

    @app.post("/v1/participants/{pid}/consents", status_code=201)
    def post_consent(pid: str, body: ConsentCreate) -> dict[str, Any]:
        return store.add_consent(pid, body)

    @app.delete("/v1/participants/{pid}/consents/{consent_id}")
    def delete_consent(pid: str, consent_id: str) -> dict[str, Any]:
        return store.withdraw_consent(pid, consent_id)

    # ------------------------------------------------------------------ devices
    @app.get("/v1/devices")
    def list_devices() -> dict[str, Any]:
        return {"devices": [{**d.model_dump(), "enabled": envelope.device_enabled(d)}
                            for d in store.devices.values()]}

    @app.post("/v1/devices", status_code=201)
    def post_device(body: DeviceDescriptor) -> dict[str, Any]:
        d = store.register_device(body)
        return {**d.model_dump(), "enabled": envelope.device_enabled(d)}

    @app.post("/v1/devices/{device_id}/calibrations", status_code=201)
    def post_calibration(device_id: str, body: Calibration) -> dict[str, Any]:
        d = store.add_calibration(device_id, body)
        return {**d.model_dump(), "enabled": envelope.device_enabled(d)}

    # ------------------------------------------------------------------ protocols
    @app.post("/v1/protocols/{protocol_id}:validate")
    def validate_protocol(protocol_id: str, body: Protocol) -> dict[str, Any]:
        violations = [v.to_dict() for v in envelope.check_protocol(body)]
        if body.protocol_id != protocol_id:
            violations.insert(0, {"rule": "ID_MISMATCH", "path": "protocol_id", "message": "path and body differ",
                                  "value": body.protocol_id, "limit": protocol_id})
        return {"protocol_id": protocol_id, "valid": not violations, "violations": violations, "stored": False}

    @app.post("/v1/protocols", status_code=201)
    def post_protocol(body: Protocol) -> dict[str, Any]:
        p = store.submit_protocol(body)
        return {"protocol_id": p.protocol_id, "version": p.version, "stored": True}

    # ------------------------------------------------------------------ sessions
    @app.post("/v1/sessions", status_code=201)
    def post_session(body: SessionCreate) -> dict[str, Any]:
        return store.session_view(store.create_session(body))

    @app.post("/v1/sessions/{sid}:arm")
    def arm(sid: str) -> dict[str, Any]:
        return store.session_view(store.arm(sid))

    @app.post("/v1/sessions/{sid}:start")
    def start(sid: str, body: StartRequest) -> dict[str, Any]:
        return store.session_view(store.start(sid, body))

    @app.post("/v1/sessions/{sid}:stop")
    async def stop(sid: str) -> JSONResponse:
        # No body model on purpose: a stop with any body (or none) is accepted. It cannot fail.
        return JSONResponse(store.stop(sid), status_code=200)

    @app.post("/v1/sessions/{sid}:clear-latch")
    def clear_latch(sid: str, body: ClearLatch) -> dict[str, Any]:
        return store.session_view(store.clear_latch(sid, body.operator_id, body.note))

    @app.post("/v1/sessions/{sid}/interactions")
    def interaction(sid: str, body: Interaction) -> dict[str, Any]:
        return store.interaction(sid, body)

    @app.post("/v1/sessions/{sid}/telemetry")
    async def telemetry(sid: str, request: Request) -> JSONResponse:
        raw = await request.body()
        frame, error = None, None
        if len(raw) > MAX_TELEMETRY_BYTES:
            error = "frame too large"
        else:
            try:
                frame = json.loads(raw)  # NaN/Infinity parse here so that they latch instead of slipping by
            except (ValueError, RecursionError):
                error = "frame is not valid JSON"
        result = store.telemetry(sid, raw[:MAX_TELEMETRY_BYTES], frame, error)
        s = store.session(sid)
        body = {"sid": sid, "action": result.action, "reason": result.reason, "detail": result.detail,
                "R": result.R, "S": result.S, "state": s.state.value, "dim_factor": s.dim_factor}
        if result.latched:
            exc = ProblemError(422, "SAFETY_LATCH", "Safety latch fired", result.detail, rule=result.reason,
                               extra={"latch": s.latch, "state": s.state.value})
            return problem_response(exc, request.url.path)
        return JSONResponse(body)

    @app.get("/v1/sessions/{sid}")
    def get_session(sid: str) -> dict[str, Any]:
        return store.session_view(store.session(sid))

    @app.get("/v1/sessions/{sid}/evidence", response_model=None)
    def evidence(sid: str, format: str = "json") -> Any:
        bundle = build_evidence(store, store.session(sid))
        if format == "markdown":
            return PlainTextResponse(bundle["markdown"], media_type="text/markdown; charset=utf-8")
        if format != "json":
            raise ProblemError(422, "SAFETY_ENVELOPE", "Unknown format", "format must be json or markdown",
                               rule="FAIL_CLOSED_VALIDATION")
        return bundle

    @app.websocket("/v1/sessions/{sid}/stream")
    async def stream(ws: WebSocket, sid: str) -> None:
        await ws.accept()
        if sid not in store.sessions:
            await ws.send_json({"type": "error", "code": "NOT_FOUND", "detail": f"session {sid} does not exist"})
            await ws.close(code=1008)
            return
        s = store.sessions[sid]
        store.tick(sid)
        await ws.send_json({"type": "snapshot", "boundary": BOUNDARY_STATEMENT, "session": store.session_view(s)})
        cursor = len(s.events)
        try:
            while True:
                try:
                    text = await asyncio.wait_for(ws.receive_text(), WS_RECEIVE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    text = None
                reply: dict[str, Any] | None = None
                if text is not None:
                    try:
                        msg = json.loads(text)
                    except ValueError:
                        msg = None
                    kind = msg.get("type") if isinstance(msg, dict) else None
                    if kind == "heartbeat":
                        reply = store.heartbeat(sid)
                    elif kind == "stop":
                        reply = {"type": "stop_ack", **store.stop(sid, reason="stream_stop")}
                    else:
                        reply = {"type": "error", "code": "UNKNOWN_MESSAGE",
                                 "detail": 'send {"type": "heartbeat"} or {"type": "stop"}'}
                store.tick(sid)
                for event in s.events[cursor:]:
                    await ws.send_json({"type": "event", "event": event})
                cursor = len(s.events)
                if reply is not None:
                    await ws.send_json(reply)
                if s.state in TERMINAL and text is None:
                    await ws.send_json({"type": "closed", "state": s.state.value})
                    await ws.close(code=1000)
                    return
        except WebSocketDisconnect:
            return

    return app
