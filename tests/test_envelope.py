# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""The safety envelope refuses every violating protocol (property tests against an independent oracle)."""

from __future__ import annotations

import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from spark_iis import envelope
from spark_iis.models import Protocol
from tests.conftest import make_world

COLOURS = st.sampled_from(["#ff0000", "#e00000", "#ff2010", "#4060a0", "#808080", "#00ff00", "#ffffff", "#000000",
                           "#ff8080", "#c00000"])
DUR = st.floats(min_value=0.5, max_value=1500.0, allow_nan=False)

visual = st.fixed_dictionaries({
    "modality": st.just("visual"),
    "pattern": st.sampled_from(["static", "fade", "flicker", "flash"]),
    "brightness_max": st.floats(0.0, 1.0),
    "colour": COLOURS,
    "field_fraction": st.floats(0.0, 1.0),
    "duration_s": DUR,
}, optional={"period_s": st.floats(0.1, 30.0), "rate_hz": st.floats(0.0, 60.0)})
audio = st.fixed_dictionaries({
    "modality": st.just("audio"),
    "pattern": st.sampled_from(["tone", "noise"]),
    "level_dba": st.floats(0.0, 120.0),
    "fade_in_s": st.floats(0.0, 20.0),
    "fade_out_s": st.floats(0.0, 20.0),
    "duration_s": DUR,
}, optional={"freq_hz": st.floats(20.0, 20000.0)})
haptic = st.fixed_dictionaries({
    "modality": st.just("haptic"),
    "pattern": st.sampled_from(["pulse", "continuous"]),
    "amplitude": st.floats(0.0, 1.0),
    "duration_s": DUR,
}, optional={"pulse_on_s": st.floats(0.05, 120.0), "pulse_off_s": st.floats(0.05, 10.0)})
claim = st.fixed_dictionaries({
    "text": st.sampled_from(["Fades are slow.", "Improves brain coherence", "A quantum effect", "Calm colours",
                             "Treats anxiety", "HBOT synergy"]),
    "source": st.just("doi:10.0000/example"),
    "tag": st.sampled_from(["SYNTHETIC", "REPORTED", "TARGET", "UNRUN"]),
})
protocols = st.fixed_dictionaries({
    "protocol_id": st.sampled_from(["calm-1", "hbot-sync", "oxygen-pace", "fade-2"]),
    "version": st.just("0.1.0"),
    "mode": st.sampled_from(["wellness_demo", "research"]),
    "steps": st.lists(st.one_of(visual, audio, haptic), min_size=1, max_size=4),
    "claims": st.lists(claim, max_size=2),
    "evidence_tag": st.just("SYNTHETIC"),
})


def _lin(c: int) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def oracle_unsafe(p: dict) -> list[str]:
    """Independent restatement of spec section 2: returns the reasons a protocol must be refused."""
    why = []
    if p["mode"] != "wellness_demo":
        why.append("research")
    if re.search("hbot|oxygen", p["protocol_id"]):
        why.append("medical")
    for s in p["steps"]:
        if s["modality"] == "visual":
            if s["pattern"] in ("flicker", "flash"):
                why.append("flashing")  # v0.1: only static and slow fades
                if s.get("rate_hz") is None or s["rate_hz"] > 3:
                    why.append("rate")
                r, g, b = (_lin(int(s["colour"][i:i + 2], 16)) for i in (1, 3, 5))
                if r + g + b > 0 and r / (r + g + b) >= 0.8:
                    why.append("red")
                if s["field_fraction"] > 0.25:
                    why.append("field")
                if "period_s" in s:
                    why.append("extra")
            elif s["pattern"] == "fade":
                if s.get("period_s") is None or s["period_s"] < 2.0:
                    why.append("ramp")
                if "rate_hz" in s:
                    why.append("extra")
            elif "period_s" in s or "rate_hz" in s:
                why.append("extra")
        elif s["modality"] == "audio":
            if s["level_dba"] > 70:
                why.append("loud")
            if s["fade_in_s"] < 5 or s["fade_out_s"] < 5 or s["fade_in_s"] + s["fade_out_s"] > s["duration_s"]:
                why.append("onset")
            if (s["pattern"] == "tone") != ("freq_hz" in s):
                why.append("freq")
        else:
            if s["pattern"] == "continuous":
                if s["duration_s"] > 60 or "pulse_on_s" in s or "pulse_off_s" in s:
                    why.append("haptic")
            elif "pulse_on_s" not in s or "pulse_off_s" not in s or s["pulse_on_s"] > 60:
                why.append("haptic")
    if max(s.get("start_s", 0) + s["duration_s"] for s in p["steps"]) > 1200:
        why.append("length")
    for c in p["claims"]:
        if c["tag"] != "REPORTED" or re.search("brain|coherence|quantum|treat|hbot", c["text"], re.I):
            why.append("claim")
    return why


@settings(max_examples=400, deadline=None)
@given(protocols)
def test_envelope_matches_oracle(p):
    violations = envelope.check_protocol(Protocol.model_validate(p))
    assert bool(violations) == bool(oracle_unsafe(p)), (violations, oracle_unsafe(p))


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(protocols)
def test_api_stores_only_safe_protocols(p):
    w = make_world()
    p = {**p, "protocol_id": p["protocol_id"] + "-x"}
    r = w.client.post("/v1/protocols", json=p)
    unsafe = oracle_unsafe(p)
    if unsafe:
        assert r.status_code == 422
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["code"] == "SAFETY_ENVELOPE"
        assert p["protocol_id"] not in w.store.protocols
    else:
        assert r.status_code == 201
    dry = w.client.post(f"/v1/protocols/{p['protocol_id']}:validate", json=p).json()
    assert dry["valid"] is (not unsafe) and dry["stored"] is False


@given(rate=st.floats(min_value=3.0001, max_value=200.0), colour=COLOURS, field=st.floats(0.0, 1.0))
def test_any_flicker_above_3hz_is_refused(rate, colour, field):
    p = Protocol.model_validate({"protocol_id": "f", "version": "0.1.0", "mode": "wellness_demo", "claims": [],
                                 "evidence_tag": "SYNTHETIC",
                                 "steps": [{"modality": "visual", "pattern": "flicker", "rate_hz": rate,
                                            "brightness_max": 0.5, "colour": colour, "field_fraction": field,
                                            "duration_s": 10.0}]})
    rules = {v.rule for v in envelope.check_protocol(p)}
    assert {"PHOTOSENSITIVITY_FLASH_RATE", "FADE_ONLY"} <= rules


@given(rate=st.floats(min_value=7.0, max_value=8.0))
def test_7_to_8_hz_is_refused_in_the_seizure_band(rate):
    assert 3.0 < rate < 30.0
    p = Protocol.model_validate({"protocol_id": "f", "version": "0.1.0", "mode": "wellness_demo", "claims": [],
                                 "evidence_tag": "SYNTHETIC",
                                 "steps": [{"modality": "visual", "pattern": "flash", "rate_hz": rate,
                                            "brightness_max": 0.1, "colour": "#4060a0", "field_fraction": 0.05,
                                            "duration_s": 10.0}]})
    assert "PHOTOSENSITIVITY_FLASH_RATE" in {v.rule for v in envelope.check_protocol(p)}


def test_red_saturation_and_field_size_at_safe_rate():
    p = Protocol.model_validate({"protocol_id": "f", "version": "0.1.0", "mode": "wellness_demo", "claims": [],
                                 "evidence_tag": "SYNTHETIC",
                                 "steps": [{"modality": "visual", "pattern": "flash", "rate_hz": 1.0,
                                            "brightness_max": 0.1, "colour": "#ff0000", "field_fraction": 0.5,
                                            "duration_s": 10.0}]})
    rules = {v.rule for v in envelope.check_protocol(p)}
    assert {"RED_SATURATION", "FIELD_SIZE", "FADE_ONLY"} <= rules
    assert "PHOTOSENSITIVITY_FLASH_RATE" not in rules


def test_saturated_red_definition():
    assert envelope.is_saturated_red("#ff0000")
    assert not envelope.is_saturated_red("#000000")
    assert not envelope.is_saturated_red("#ff8080")
    assert not envelope.is_saturated_red("#4060a0")


def test_flash_without_rate_is_refused():
    p = Protocol.model_validate({"protocol_id": "f", "version": "0.1.0", "mode": "wellness_demo", "claims": [],
                                 "evidence_tag": "SYNTHETIC",
                                 "steps": [{"modality": "visual", "pattern": "flicker", "brightness_max": 0.1,
                                            "colour": "#4060a0", "field_fraction": 0.1, "duration_s": 10.0}]})
    assert "MISSING_VALUE" in {v.rule for v in envelope.check_protocol(p)}


def test_limits_are_tagged():
    assert all(v["tag"] == "TARGET" for v in envelope.LIMITS.values())
    assert envelope.LIMITS["max_flash_hz"]["value"] == 3.0
