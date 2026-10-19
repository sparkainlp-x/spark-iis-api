# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Residual latch (spec section 6), reusing the Spark residual contract.

* One telemetry block = 32 values (OES-32); OES-512 = 16 blocks of 32.
* ``R = max|x - x_ref|``; ``S = 0.45·Peak + 0.35·RMS + 0.20·MeanAbs`` of ``x - x_ref``.
* ``S >= tau`` (default 0.50) latches: the session goes to LATCHED and output stops.
* NaN, inf, a wrong length, a malformed frame, a stale reference or a missing sensor is a latch, never a pass.
* ``DIM_THRESHOLD <= S < tau`` dims output (TARGET). Sensors can only dim or stop; nothing here can raise output.

The latch is a **stop bit only**. It is not a measure of wellbeing, coherence or brain state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

BLOCK_WIDTH = 32
DEFAULT_TAU = 0.50
DIM_THRESHOLD = 0.35  # TARGET
WEIGHTS = (0.45, 0.35, 0.20)
FRAME_KEYS = {"sid", "t_ms", "channel", "x", "tag"}
TAGS = {"SYNTHETIC", "REPORTED", "TARGET", "UNRUN"}


@dataclass(frozen=True)
class FrameResult:
    action: str  # "none" | "dim" | "latch"
    reason: str  # "ok" | "residual_latch" | "dim" | "invalid_frame" | "stale_reference" | ...
    detail: str
    R: float | None = None
    S: float | None = None
    channel: str | None = None
    t_ms: int | None = None

    @property
    def latched(self) -> bool:
        return self.action == "latch"


def residual_max(x: Sequence[float], x_ref: Sequence[float]) -> float:
    return max(abs(a - b) for a, b in zip(x, x_ref, strict=True))


def latch_score(x: Sequence[float], x_ref: Sequence[float]) -> float:
    d = [abs(a - b) for a, b in zip(x, x_ref, strict=True)]
    rms = math.sqrt(math.fsum(v * v for v in d) / len(d))
    return WEIGHTS[0] * max(d) + WEIGHTS[1] * rms + WEIGHTS[2] * (math.fsum(d) / len(d))


def _bad(detail: str, reason: str = "invalid_frame", **kw: Any) -> FrameResult:
    return FrameResult("latch", reason, detail, **kw)


def _finite_block(values: Any, what: str) -> tuple[list[float] | None, str]:
    if not isinstance(values, list):
        return None, f"{what} must be an array of {BLOCK_WIDTH} numbers"
    if len(values) != BLOCK_WIDTH:
        return None, f"{what} must contain exactly {BLOCK_WIDTH} values (got {len(values)})"
    out = []
    for i, v in enumerate(values):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, f"{what}[{i}] is not a number"
        try:
            f = float(v)
        except OverflowError:
            return None, f"{what}[{i}] is outside the float range"
        if not math.isfinite(f):
            return None, f"{what}[{i}] is not finite"
        out.append(f)
    return out, ""


def evaluate_frame(
    frame: Any,
    *,
    sid: str,
    channels: Mapping[str, Sequence[float]],
    last_t_ms: Mapping[str, int],
    reference_fresh: bool,
    tau: float = DEFAULT_TAU,
) -> FrameResult:
    """Validate and score one telemetry frame. Any defect returns ``action="latch"`` (fail closed)."""
    if not isinstance(frame, dict):
        return _bad("frame must be a JSON object")
    keys = set(frame)
    if keys != FRAME_KEYS:
        missing, extra = sorted(FRAME_KEYS - keys), sorted(keys - FRAME_KEYS)
        return _bad(f"frame fields: missing {missing}, unknown {extra}")
    channel, t_ms = frame["channel"], frame["t_ms"]
    if frame["sid"] != sid:
        return _bad("frame sid does not match the session")
    if not isinstance(channel, str) or channel not in channels:
        return _bad("unknown sensor channel", reason="unknown_channel")
    if isinstance(t_ms, bool) or not isinstance(t_ms, int) or t_ms < 0:
        return _bad("t_ms must be a non-negative integer", channel=channel)
    if t_ms <= last_t_ms.get(channel, -1):
        return _bad("t_ms must increase per channel (stale or replayed frame)", channel=channel, t_ms=t_ms)
    if not isinstance(frame["tag"], str) or frame["tag"] not in TAGS:
        return _bad("tag must be SYNTHETIC, REPORTED, TARGET or UNRUN", channel=channel, t_ms=t_ms)
    if not reference_fresh:
        return _bad("the reference changed after ARM (stale reference)", reason="stale_reference",
                    channel=channel, t_ms=t_ms)
    x, problem = _finite_block(frame["x"], "x")
    if x is None:
        return _bad(problem, channel=channel, t_ms=t_ms)
    ref = channels[channel]
    try:
        r, s = residual_max(x, ref), latch_score(x, ref)
    except (OverflowError, ValueError):
        r = s = math.inf
    if not (math.isfinite(r) and math.isfinite(s)):
        return _bad("residual overflowed", channel=channel, t_ms=t_ms)
    if s >= tau:
        return FrameResult("latch", "residual_latch", f"S >= tau ({tau})", r, s, channel, t_ms)
    if s >= DIM_THRESHOLD:
        return FrameResult("dim", "dim", f"S >= dim threshold ({DIM_THRESHOLD}, TARGET)", r, s, channel, t_ms)
    return FrameResult("none", "ok", "S < dim threshold", r, s, channel, t_ms)
