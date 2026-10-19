# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""No path raises intensity from sensor data: telemetry can only dim or stop."""

from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.conftest import make_world

offsets = st.lists(st.one_of(st.floats(-2.0, 2.0), st.just(float("nan")), st.just(1e300)), min_size=1, max_size=12)


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(offsets=offsets, dims=st.lists(st.booleans(), min_size=12, max_size=12))
def test_telemetry_never_raises_output(offsets, dims):
    with_t, without = make_world(), make_world()
    a, b = with_t.running(), without.running(devices=["sim-display", "sim-audio"])
    last_dim = 1.0
    for i, off in enumerate(offsets):
        t = (i + 1) * 200
        for w, sid in ((with_t, a), (without, b)):
            w.clock.advance(200)
            w.beat(sid)
            if dims[i]:
                w.client.post(f"/v1/sessions/{sid}/interactions", json={"input": "switch", "intent": "dim"})
        x = [v + off for v in with_t.reference]
        with_t.client.post(f"/v1/sessions/{a}/telemetry", content=_raw(a, t, x),
                           headers={"content-type": "application/json"})
        sa, sb = with_t.store.sessions[a], without.store.sessions[b]
        oa, ob = with_t.store.output(sa), without.store.output(sb)
        for k in oa:
            assert oa[k] <= ob[k] + 1e-12, (k, oa, ob)
        assert sa.dim_factor <= last_dim
        if sa.state.value == "RUNNING":
            assert sa.dim_factor <= sb.dim_factor
        last_dim = sa.dim_factor


def _raw(sid, t, x):
    return json.dumps({"sid": sid, "t_ms": t, "channel": "oes-0", "x": x, "tag": "SYNTHETIC"})


def test_no_raise_intent_exists():
    from typing import get_args

    from spark_iis.models import Interaction

    intents = set(get_args(Interaction.model_fields["intent"].annotation))
    assert intents == {"stop", "dim", "comfort_rating", "acknowledge"}
