# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Request and record models (Pydantic v2). Every model forbids unknown fields (FAIL-CLOSED).

Floats reject NaN/inf, numeric fields reject strings and booleans (strict mode), and enumerations are
closed: an unknown unit, device kind, modality, pattern or intent is refused.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Tag = Literal["SYNTHETIC", "REPORTED", "TARGET", "UNRUN"]
Ident = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
HexColour = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


# --------------------------------------------------------------------------- participants
InputMethod = Literal["switch", "gaze", "sip_and_puff", "voice", "touch", "keyboard"]


class ParticipantCreate(Strict):
    """Pseudonymous participant: there is deliberately no field for a name, contact detail or health record."""

    label: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9 _-]{0,32}$")] = ""


class Preferences(Strict):
    input_methods: Annotated[list[InputMethod], Field(min_length=1, max_length=6)]
    max_brightness: Unit
    sound_off: bool
    captions: bool
    pace: Literal["slow", "normal"]


Answer = Literal["yes", "no", "unsure"]


class Screening(Strict):
    """Photosensitivity and comfort questionnaire. Any "yes" or "unsure" blocks all visual modulation."""

    questionnaire_version: Literal["0.1"]
    photosensitive_seizure_history: Answer
    light_triggered_migraine: Answer
    discomfort_with_flashing_or_patterns: Answer
    sound_sensitivity: Answer


class ConsentCreate(Strict):
    consent_version: Literal["0.1"]
    plain_language_text_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    agreed: Literal[True]
    input_used: InputMethod


# --------------------------------------------------------------------------- devices
DeviceKind = Literal["holographic_display", "display", "audio", "haptic", "sensor"]


class DeviceLimits(Strict):
    max_flash_hz: Annotated[float, Field(ge=0.0)] = 0.0
    max_brightness: Unit = 0.0
    max_level_dba: Annotated[float, Field(ge=0.0)] = 0.0
    max_amplitude: Unit = 0.0


class Calibration(Strict):
    """Calibration record. Simulated devices may carry SYNTHETIC calibrations; REPORTED needs an instrument."""

    date: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    instrument: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    tag: Tag
    reference: dict[Ident, Annotated[list[float], Field(min_length=32, max_length=32)]] = {}


class DeviceDescriptor(Strict):
    device_id: Ident
    kind: DeviceKind
    limits: DeviceLimits
    can_actuate: bool
    simulated: Literal[True]
    channels: Annotated[list[Ident], Field(max_length=16)] = []
    calibration: Calibration | None = None


# --------------------------------------------------------------------------- protocols
class VisualStep(Strict):
    modality: Literal["visual"]
    pattern: Literal["static", "fade", "flicker", "flash"]
    period_s: Annotated[float, Field(gt=0.0)] | None = None  # fade: full cycle; ramp = period_s / 2
    rate_hz: Annotated[float, Field(ge=0.0)] | None = None  # flicker/flash
    brightness_max: Unit
    colour: HexColour
    field_fraction: Unit
    duration_s: Annotated[float, Field(gt=0.0)]
    start_s: Annotated[float, Field(ge=0.0)] = 0.0


class AudioStep(Strict):
    modality: Literal["audio"]
    pattern: Literal["tone", "noise"]
    freq_hz: Annotated[float, Field(ge=20.0, le=20000.0)] | None = None
    level_dba: Annotated[float, Field(ge=0.0)]
    fade_in_s: Annotated[float, Field(ge=0.0)]
    fade_out_s: Annotated[float, Field(ge=0.0)]
    duration_s: Annotated[float, Field(gt=0.0)]
    start_s: Annotated[float, Field(ge=0.0)] = 0.0


class HapticStep(Strict):
    modality: Literal["haptic"]
    pattern: Literal["pulse", "continuous"]
    amplitude: Unit
    pulse_on_s: Annotated[float, Field(gt=0.0)] | None = None
    pulse_off_s: Annotated[float, Field(gt=0.0)] | None = None
    duration_s: Annotated[float, Field(gt=0.0)]
    start_s: Annotated[float, Field(ge=0.0)] = 0.0


Step = Annotated[VisualStep | AudioStep | HapticStep, Field(discriminator="modality")]


class Claim(Strict):
    text: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    source: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    tag: Tag


class Protocol(Strict):
    protocol_id: Ident
    version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    mode: Literal["wellness_demo", "research"]
    steps: Annotated[list[Step], Field(min_length=1, max_length=32)]
    claims: list[Claim]
    evidence_tag: Tag


# --------------------------------------------------------------------------- sessions
class SessionCreate(Strict):
    participant_id: Annotated[str, StringConstraints(pattern=r"^p-[0-9a-f]{12}$")]
    protocol_id: Ident
    device_ids: Annotated[list[Ident], Field(min_length=1, max_length=8)]
    seed: int | None = None


class Confirmation(Strict):
    input: InputMethod
    intent: Literal["start"]


class StartRequest(Strict):
    confirmation: Confirmation


class Interaction(Strict):
    input: InputMethod
    intent: Literal["stop", "dim", "comfort_rating", "acknowledge"]
    comfort_rating: Annotated[int, Field(ge=1, le=5)] | None = None


class ClearLatch(Strict):
    operator_id: Ident
    note: Annotated[str, StringConstraints(min_length=1, max_length=500)]
