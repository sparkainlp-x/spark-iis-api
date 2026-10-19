# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""The v0.1 hard safety envelope (spec section 2). Not configurable.

``check_protocol`` returns *every* violation, so a dry run shows the full picture. A protocol is accepted
only when the list is empty. Limits are design limits (TARGET); the 3 Hz flash threshold follows WCAG 2.3.1.

Why 7-8 Hz flicker is refused: the photosensitive-seizure band is roughly 3-30 Hz, and WCAG 2.3.1 sets
the general flash threshold at 3 flashes per second. In v0.1 only static output and slow fades (each ramp
at least 1 s) are allowed. Flicker and flash patterns are refused at every rate, with the specific rules
(rate, red saturation, field size) reported as well so the refusal is explained.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .models import AudioStep, DeviceDescriptor, HapticStep, Preferences, Protocol, Screening, VisualStep

MAX_FLASH_HZ = 3.0  # WCAG 2.3.1 general flash threshold
MIN_FADE_RAMP_S = 1.0
MAX_FLASHING_FIELD_FRACTION = 0.25
MAX_AUDIO_DBA = 70.0  # TARGET; must be calibrated
MIN_AUDIO_FADE_S = 5.0
MAX_CONTINUOUS_HAPTIC_S = 60.0
MAX_SESSION_S = 20 * 60.0  # TARGET
MANDATORY_BREAK_S = 10 * 60.0
RED_RATIO = 0.8  # WCAG "saturated red": R / (R + G + B) >= 0.8 on linearised sRGB

LIMITS: dict[str, dict[str, Any]] = {
    "max_flash_hz": {"value": MAX_FLASH_HZ, "unit": "Hz", "tag": "TARGET", "source": "WCAG 2.3.1"},
    "min_fade_ramp_s": {"value": MIN_FADE_RAMP_S, "unit": "s", "tag": "TARGET"},
    "max_flashing_field_fraction": {"value": MAX_FLASHING_FIELD_FRACTION, "unit": "fraction", "tag": "TARGET"},
    "max_audio_dba": {"value": MAX_AUDIO_DBA, "unit": "dB(A)", "tag": "TARGET"},
    "min_audio_fade_s": {"value": MIN_AUDIO_FADE_S, "unit": "s", "tag": "TARGET"},
    "max_continuous_haptic_s": {"value": MAX_CONTINUOUS_HAPTIC_S, "unit": "s", "tag": "TARGET"},
    "max_session_s": {"value": MAX_SESSION_S, "unit": "s", "tag": "TARGET"},
    "mandatory_break_s": {"value": MANDATORY_BREAK_S, "unit": "s", "tag": "TARGET"},
    "heartbeat_timeout_ms": {"value": 500, "unit": "ms", "tag": "TARGET"},
    "stop_ramp_ms": {"value": 1000, "unit": "ms", "tag": "TARGET"},
}

#: Words that may never appear in a device or protocol: the API never touches medical devices or HBOT.
MEDICAL_PATTERN = re.compile(r"hyperbaric|hbot|oxygen|\bo2\b|medical|chamber|ventilator|infusion", re.IGNORECASE)
#: Claims that are refused regardless of citation (boundary statement).
FORBIDDEN_CLAIM_PATTERN = re.compile(
    r"therap|treat|cure|diagnos|prevent|heal|neuro|brain|coherence|hbot|hyperbaric|quantum|seizure|medical",
    re.IGNORECASE,
)
VISUAL_KINDS = ("display", "holographic_display")


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    message: str
    value: Any = None
    limit: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _linear(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def is_saturated_red(colour: str) -> bool:
    r, g, b = (_linear(int(colour[i : i + 2], 16)) for i in (1, 3, 5))
    total = r + g + b
    return total > 0.0 and r / total >= RED_RATIO


def protocol_duration_s(protocol: Protocol) -> float:
    return max(step.start_s + step.duration_s for step in protocol.steps)


def _visual(step: VisualStep, path: str, blocked: bool) -> Iterable[Violation]:
    flashing = step.pattern in ("flicker", "flash")
    if step.pattern == "fade":
        if step.period_s is None:
            yield Violation("MISSING_VALUE", f"{path}.period_s", "a fade needs period_s")
        elif step.period_s / 2.0 < MIN_FADE_RAMP_S:
            yield Violation(
                "FADE_RAMP",
                f"{path}.period_s",
                "each fade ramp must last at least 1 s",
                step.period_s,
                2 * MIN_FADE_RAMP_S,
            )
        if step.rate_hz is not None:
            yield Violation("UNEXPECTED_FIELD", f"{path}.rate_hz", "rate_hz is not used by a fade")
    elif step.pattern == "static":
        for name in ("period_s", "rate_hz"):
            if getattr(step, name) is not None:
                yield Violation("UNEXPECTED_FIELD", f"{path}.{name}", f"{name} is not used by static output")
    if flashing:
        yield Violation(
            "FADE_ONLY",
            f"{path}.pattern",
            "v0.1 allows only static output and slow fades",
            step.pattern,
            ["static", "fade"],
        )
        if step.rate_hz is None:
            yield Violation("MISSING_VALUE", f"{path}.rate_hz", "a flicker or flash needs rate_hz")
        elif step.rate_hz > MAX_FLASH_HZ:
            yield Violation(
                "PHOTOSENSITIVITY_FLASH_RATE",
                f"{path}.rate_hz",
                "luminance/red flicker above 3 Hz is refused (WCAG 2.3.1; 3-30 Hz seizure band)",
                step.rate_hz,
                MAX_FLASH_HZ,
            )
        if step.period_s is not None:
            yield Violation("UNEXPECTED_FIELD", f"{path}.period_s", "period_s is not used by a flicker or flash")
        if is_saturated_red(step.colour):
            yield Violation(
                "RED_SATURATION", f"{path}.colour", "saturated red flashes are refused at any rate", step.colour
            )
        if step.field_fraction > MAX_FLASHING_FIELD_FRACTION:
            yield Violation(
                "FIELD_SIZE",
                f"{path}.field_fraction",
                "a flashing region over 25% of the field of view is refused",
                step.field_fraction,
                MAX_FLASHING_FIELD_FRACTION,
            )
    if blocked and step.pattern != "static":
        yield Violation(
            "VISUAL_MODULATION_BLOCKED",
            f"{path}.pattern",
            'screening answered "yes" or "unsure": all visual modulation is blocked',
            step.pattern,
        )


def _audio(step: AudioStep, path: str) -> Iterable[Violation]:
    if step.level_dba > MAX_AUDIO_DBA:
        yield Violation(
            "AUDIO_LEVEL",
            f"{path}.level_dba",
            "audio above 70 dB(A) (TARGET) is refused",
            step.level_dba,
            MAX_AUDIO_DBA,
        )
    for name in ("fade_in_s", "fade_out_s"):
        if getattr(step, name) < MIN_AUDIO_FADE_S:
            yield Violation(
                "AUDIO_FADE",
                f"{path}.{name}",
                "audio needs 5 s fades (no sudden onsets)",
                getattr(step, name),
                MIN_AUDIO_FADE_S,
            )
    if step.fade_in_s + step.fade_out_s > step.duration_s:
        yield Violation("AUDIO_FADE", f"{path}.duration_s", "fades longer than the step", step.duration_s)
    if step.pattern == "tone" and step.freq_hz is None:
        yield Violation("MISSING_VALUE", f"{path}.freq_hz", "a tone needs freq_hz")
    if step.pattern == "noise" and step.freq_hz is not None:
        yield Violation("UNEXPECTED_FIELD", f"{path}.freq_hz", "freq_hz is not used by noise")


def _haptic(step: HapticStep, path: str) -> Iterable[Violation]:
    if step.pattern == "continuous":
        if step.duration_s > MAX_CONTINUOUS_HAPTIC_S:
            yield Violation(
                "HAPTIC_CONTINUOUS",
                f"{path}.duration_s",
                "continuous vibration over 60 s is refused",
                step.duration_s,
                MAX_CONTINUOUS_HAPTIC_S,
            )
        for name in ("pulse_on_s", "pulse_off_s"):
            if getattr(step, name) is not None:
                yield Violation("UNEXPECTED_FIELD", f"{path}.{name}", f"{name} is not used by continuous vibration")
    else:
        if step.pulse_on_s is None or step.pulse_off_s is None:
            yield Violation("MISSING_VALUE", f"{path}.pulse_on_s", "a pulse needs pulse_on_s and pulse_off_s")
        elif step.pulse_on_s > MAX_CONTINUOUS_HAPTIC_S:
            yield Violation(
                "HAPTIC_CONTINUOUS",
                f"{path}.pulse_on_s",
                "a pulse longer than 60 s is continuous",
                step.pulse_on_s,
                MAX_CONTINUOUS_HAPTIC_S,
            )


def check_protocol(
    protocol: Protocol,
    *,
    screening: Screening | None = None,
    preferences: Preferences | None = None,
    devices: Sequence[DeviceDescriptor] | None = None,
) -> list[Violation]:
    """Every envelope violation of ``protocol`` (optionally in the context of a participant and devices)."""
    out: list[Violation] = []
    if protocol.mode != "wellness_demo":
        out.append(
            Violation(
                "RESEARCH_MODE_OUT_OF_SCOPE",
                "mode",
                "research mode needs REB approval and a separate signed build; out of scope in v0.1",
                protocol.mode,
            )
        )
    if MEDICAL_PATTERN.search(protocol.protocol_id):
        out.append(
            Violation("MEDICAL_DEVICE", "protocol_id", "the API never times against or controls medical devices")
        )
    blocked = screening is not None and blocks_visual_modulation(screening)
    for i, step in enumerate(protocol.steps):
        path = f"steps[{i}]"
        if isinstance(step, VisualStep):
            out += _visual(step, path, blocked)
            if preferences is not None and step.brightness_max > preferences.max_brightness:
                out.append(
                    Violation(
                        "BRIGHTNESS_PREFERENCE",
                        f"{path}.brightness_max",
                        "above the participant's max_brightness",
                        step.brightness_max,
                        preferences.max_brightness,
                    )
                )
        elif isinstance(step, AudioStep):
            out += _audio(step, path)
            if preferences is not None and preferences.sound_off:
                out.append(Violation("SOUND_OFF_PREFERENCE", path, "the participant has turned sound off"))
        else:
            out += _haptic(step, path)
    duration = protocol_duration_s(protocol)
    if duration > MAX_SESSION_S:
        out.append(
            Violation("SESSION_LENGTH", "steps", "sessions are limited to 20 min (TARGET)", duration, MAX_SESSION_S)
        )
    for i, claim in enumerate(protocol.claims):
        if FORBIDDEN_CLAIM_PATTERN.search(claim.text):
            out.append(
                Violation(
                    "CLAIM_FORBIDDEN",
                    f"claims[{i}].text",
                    "therapeutic, neurological, coherence, HBOT or quantum claims are refused",
                )
            )
        if claim.tag != "REPORTED":
            out.append(
                Violation(
                    "CLAIM_UNSUPPORTED",
                    f"claims[{i}].tag",
                    "a claim needs a cited REPORTED source",
                    claim.tag,
                    "REPORTED",
                )
            )
    if devices is not None:
        out += check_devices_for_protocol(protocol, devices)
    return out


def check_device(device: DeviceDescriptor) -> list[Violation]:
    out = []
    if MEDICAL_PATTERN.search(device.device_id):
        out.append(Violation("MEDICAL_DEVICE", "device_id", "medical devices, HBOT and oxygen are never integrated"))
    if device.kind == "sensor":
        if device.can_actuate:
            out.append(Violation("SENSOR_READ_ONLY", "can_actuate", "sensors are read-only"))
        if not device.channels:
            out.append(Violation("MISSING_VALUE", "channels", "a sensor must declare at least one channel"))
    elif device.channels:
        out.append(Violation("UNEXPECTED_FIELD", "channels", "only sensors have channels"))
    if device.kind == "haptic" and device.limits.max_amplitude <= 0.0:
        out.append(Violation("MISSING_VALUE", "limits.max_amplitude", "a haptic device needs an amplitude cap"))
    if device.calibration is not None:
        out += check_calibration(device, device.calibration)
    return out


def check_calibration(device: DeviceDescriptor, calibration) -> list[Violation]:
    out = []
    if calibration.tag == "REPORTED" and calibration.instrument.strip().upper() in {"UNRUN", "SIMULATED", "NONE"}:
        out.append(Violation("CALIBRATION_INSTRUMENT", "instrument", "a REPORTED calibration names its instrument"))
    if device.kind == "sensor":
        missing = sorted(set(device.channels) - set(calibration.reference))
        if missing:
            out.append(
                Violation("MISSING_REFERENCE", "reference", "every sensor channel needs a 32-value reference", missing)
            )
    elif calibration.reference:
        out.append(Violation("UNEXPECTED_FIELD", "reference", "only sensors have reference vectors"))
    return out


def device_enabled(device: DeviceDescriptor) -> bool:
    """A device is enabled only with a REPORTED calibration, or a SYNTHETIC one on a simulated device."""
    cal = device.calibration
    return cal is not None and (cal.tag == "REPORTED" or (cal.tag == "SYNTHETIC" and device.simulated))


def check_devices_for_protocol(protocol: Protocol, devices: Sequence[DeviceDescriptor]) -> list[Violation]:
    out = []
    for d in devices:
        if not device_enabled(d):
            out.append(Violation("DEVICE_UNCALIBRATED", f"devices.{d.device_id}", "device has no usable calibration"))
        out += [
            Violation(v.rule, f"devices.{d.device_id}.{v.path}", v.message, v.value, v.limit) for v in check_device(d)
        ]
    for i, step in enumerate(protocol.steps):
        kinds = VISUAL_KINDS if isinstance(step, VisualStep) else (step.modality,)
        actuators = [d for d in devices if d.kind in kinds and d.can_actuate]
        if not actuators:
            out.append(Violation("MISSING_DEVICE", f"steps[{i}]", f"no actuating {'/'.join(kinds)} device in session"))
        for d in actuators:
            if isinstance(step, VisualStep) and step.brightness_max > d.limits.max_brightness:
                out.append(
                    Violation(
                        "DEVICE_BRIGHTNESS",
                        f"steps[{i}].brightness_max",
                        f"above {d.device_id} limit",
                        step.brightness_max,
                        d.limits.max_brightness,
                    )
                )
            if isinstance(step, AudioStep) and step.level_dba > d.limits.max_level_dba:
                out.append(
                    Violation(
                        "DEVICE_AUDIO_LEVEL",
                        f"steps[{i}].level_dba",
                        f"above {d.device_id} limit",
                        step.level_dba,
                        d.limits.max_level_dba,
                    )
                )
            if isinstance(step, HapticStep) and step.amplitude > d.limits.max_amplitude:
                out.append(
                    Violation(
                        "HAPTIC_AMPLITUDE",
                        f"steps[{i}].amplitude",
                        f"above {d.device_id} cap",
                        step.amplitude,
                        d.limits.max_amplitude,
                    )
                )
    return out


def blocks_visual_modulation(screening: Screening) -> bool:
    answers = (
        screening.photosensitive_seizure_history,
        screening.light_triggered_migraine,
        screening.discomfort_with_flashing_or_patterns,
    )
    return any(a in ("yes", "unsure") for a in answers)
