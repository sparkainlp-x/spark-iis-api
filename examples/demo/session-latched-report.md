# Evidence report: session `s-9019097b2ffd`

> This software is not a medical device. It does not diagnose, treat, cure or prevent any condition. No therapeutic, neurological, "coherence", HBOT-synergy or quantum effect is claimed or established. All numbers are tagged SYNTHETIC, REPORTED, TARGET or UNRUN.

All device output in this report is **simulated**. No hardware was driven.

| Field | Value |
|---|---|
| Protocol | `fade-calm-01` 0.1.0 (evidence tag SYNTHETIC) |
| Seed | 42 |
| Final state | **LATCHED** (residual_latch) |
| Planned duration | 60.0 s (TARGET) |
| Elapsed running time | 5.0 s (SYNTHETIC) |
| Final dim factor | 1.0 (SYNTHETIC) |
| Frames received / raw retained | 5 (SYNTHETIC) / 5 (SYNTHETIC) |
| Frames dimmed / latched | 0 (SYNTHETIC) / 1 (SYNTHETIC) |
| Max R / max S | 0.8000000000000002 (SYNTHETIC) / 0.8000000000000002 (SYNTHETIC) |
| tau / dim threshold | 0.5 (TARGET) / 0.35 (TARGET) |

## Transitions

- t=0 ms: CREATED -> SCREENED (screening_on_file)
- t=0 ms: SCREENED -> CONSENTED (consent_active)
- t=0 ms: CONSENTED -> ARMED (envelope_ok)
- t=0 ms: ARMED -> RUNNING (participant_confirmed)
- t=5000 ms: RUNNING -> LATCHED (residual_latch)

## Latch

- reason: `residual_latch`; detail: S >= tau (0.5); action: stop

## Not measured

- any physiological, neurological or health outcome
- comfort, wellbeing, relaxation or 'coherence'
- real light, sound or vibration output (devices are simulated)
- latency on real hardware

## Provenance

- code: spark-iis-api 0.1.0, source tree sha256 `1299f32a9cc85f3006e4d27ae92bae6d5ef71dcc0548d65a20c15801f27f9f55`, commit `unrecorded`
- protocol sha256 `9f9e95a20851a93cc040b3fc3326794b94d8188e533bcefc0abab7ce6ba850cc`
- event log sha256 `d02733e873ad18289d5501eba1fec478af3c42e7ff771c275d8c3dcd6fac88e0`
- telemetry hash-list sha256 `9fd04347ea36abbe634f186da85b03301933a8750d09800214e44d1f935c79f1`
- report sha256 `1d369d54b30c37a902f8a8f39114acb7498d9788f2306dc9c2b40637ea9f9135`
