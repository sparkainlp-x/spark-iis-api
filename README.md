# spark-iis-api

Fail-closed reference server for the **Spark Integration–Interaction–Stimulation (IIS) API** v0.1:
FastAPI + Pydantic v2, Python 3.10–3.13, with **simulated** display, audio, haptic and sensor devices only.
There are no hardware drivers.

> **Boundary statement**
> This software is not a medical device. It does not diagnose, treat, cure or prevent any condition.
> No therapeutic, neurological, "coherence", HBOT-synergy or quantum effect is claimed or established.
> All numbers are tagged SYNTHETIC, REPORTED, TARGET or UNRUN.

The same statement is sent on every HTTP response in the `X-Spark-Boundary` header, in the first message of every
WebSocket stream, and at the top of every evidence report.

**Evidence tags used below:** SYNTHETIC = produced by the seed-42 simulated demo; REPORTED = measured on named real
data with provenance (none in this repository); TARGET = a design limit or goal; UNRUN = not yet run.

## What it does

1. **Integrate** simulated devices (display, holographic display, audio, haptic, read-only sensor) with capability
   descriptors and calibration records. A device without a usable calibration stays disabled.
2. **Interact** through accessible inputs (switch, gaze, sip-and-puff, voice, touch, keyboard). **Stop is accepted
   from any input, in any state.**
3. **Stimulate** (compute simulated light, sound and vibration levels) only inside a locked safety envelope.
4. **Archive** every session as a hash-chained audit log, a provenance manifest and a JSON/Markdown evidence report.

The API never controls, reads from or times against a hyperbaric chamber, an oxygen supply or any medical device.
Device or protocol IDs that look like one (`hbot`, `hyperbaric`, `oxygen`, `chamber`, …) are refused.

## Hard safety locks (v0.1, not configurable)

| Lock | Rule (tag) | Refusal rule code |
|---|---|---|
| Photosensitivity | No luminance or red flicker above **3 Hz** (TARGET, WCAG 2.3.1). v0.1 allows only static output and slow fades with ramps ≥ **1 s** (TARGET); any flicker or flash pattern is refused | `PHOTOSENSITIVITY_FLASH_RATE`, `FADE_ONLY`, `FADE_RAMP` |
| Red saturation | Saturated red (linear-sRGB R/(R+G+B) ≥ **0.8**, TARGET, WCAG definition) flashes are refused at any rate | `RED_SATURATION` |
| Field size | A flashing region over **25%** (TARGET) of the field of view is refused | `FIELD_SIZE` |
| Audio | Max **70 dB(A)** (TARGET; must be calibrated), **5 s** (TARGET) fade in and out, fades no longer than the step | `AUDIO_LEVEL`, `AUDIO_FADE` |
| Haptics | Amplitude cap per device descriptor; no continuous vibration over **60 s** (TARGET) | `HAPTIC_AMPLITUDE`, `HAPTIC_CONTINUOUS` |
| Medical devices / HBOT | Never integrated | `MEDICAL_DEVICE` |
| Closed loop | Sensors can only **dim** or **stop**; nothing raises intensity or rate | (by construction; property-tested) |
| Session length | Max **20 min** (TARGET), then a mandatory **10 min** (TARGET) break | `SESSION_LENGTH`, `BREAK_REQUIRED` |
| Heartbeat | Missing for more than **500 ms** (TARGET): output stops | state `STOPPED`, reason `heartbeat_missed` |
| Unknown | Any unknown field, unit, device kind or missing value is refused (`extra="forbid"`, strict types, no NaN/inf) | `FAIL_CLOSED_VALIDATION` |
| Screening | A "yes" or "unsure" on any photosensitivity/flashing question blocks all visual modulation | `VISUAL_MODULATION_BLOCKED` |
| Claims | `claims` is empty by default; a claim needs a REPORTED cited source; therapeutic/neuro/coherence/HBOT/quantum claims are refused | `CLAIM_UNSUPPORTED`, `CLAIM_FORBIDDEN` |

### Why 7–8 Hz flicker is refused

Flicker at 7–8 Hz (for example the often-quoted "7.83 Hz") lies inside the **3–30 Hz** band in which flashing light
is most likely to provoke seizures in people with photosensitive epilepsy. WCAG 2.3.1 ("Three Flashes or Below
Threshold") sets **3 flashes per second** as the general flash threshold. v0.1 therefore refuses any luminance or red
flicker above 3 Hz, and in fact refuses every flicker or flash pattern: only static output and slow fades are
allowed. No entrainment, "coherence" or other effect of any frequency is claimed. Allowing anything outside these
limits would be a separate **research mode** (ethics-board approval ID, named operator, documented screening and a
separate signed build), which is out of scope for v0.1.

## Session state machine

```
CREATED -> SCREENED -> CONSENTED -> ARMED -> RUNNING -> COMPLETED
                                      |         |-> STOPPED   (participant/operator/heartbeat/consent stop)
                                      |         |-> LATCHED   (safety latch fired)
                                      '-> REFUSED (validation failed)
```

- `:arm` works only from `CREATED`. It walks through `SCREENED` (latest screening on file) and `CONSENTED` (active
  consent), then re-checks preferences, device calibration, the full envelope in the participant's and devices'
  context, uncleared latches, concurrent sessions and the break rule. Any failure moves to `REFUSED` with every
  violation listed.
- `:start` needs a participant confirmation from one of the participant's own input methods.
- `STOPPED`, `LATCHED`, `COMPLETED` and `REFUSED` are terminal. There is no auto-resume; a new session needs a new ARM.
- `:stop` is **always accepted, idempotent and cannot fail**: it returns 200 for any body, any state, and even an
  unknown session ID, and output goes to zero immediately (spec TARGET: within 1 s). If an internal error happens,
  the session is forced to `STOPPED`.
- A LATCHED session can only be cleared by an operator (`:clear-latch`); clearing is archived, the state stays
  `LATCHED`, and the participant cannot ARM a new session until it is cleared.

## Residual latch

Telemetry frames are read-only 32-value blocks: `{"sid", "t_ms", "channel", "x": [32 finite numbers], "tag"}`.

- `R = max|x − x_ref|`; `S = 0.45·Peak + 0.35·RMS + 0.20·MeanAbs` of `x − x_ref`.
- `S ≥ τ` (**0.50**, TARGET) → `LATCHED`, output stops. `0.35 ≤ S < τ` (**0.35**, TARGET) → output is halved (dim).
- NaN, ±inf, a wrong length, a non-number, an unknown field or channel, a replayed `t_ms`, invalid JSON, a stale
  reference (calibration changed after ARM) or a **missing sensor** (silent for more than **1000 ms**, TARGET) all
  latch. They never pass.
- The latch is a **stop bit only**. It is not a measure of wellbeing, coherence or brain state.
- A participant stop, a comfort rating ≤ **2** out of **5**, or a missed heartbeat also stop output, independently of
  the latch.

## Endpoints (`/v1`)

| Method | Path | Notes |
|---|---|---|
| GET | `/v1` | Service info, boundary statement and every limit with its tag |
| POST | `/v1/participants` | Pseudonymous: only an optional short label; no name, contact or health fields exist |
| PUT | `/v1/participants/{pid}/preferences` | Input methods, max brightness, sound off, captions, pace |
| POST | `/v1/participants/{pid}/screenings` | Photosensitivity and comfort questionnaire |
| POST | `/v1/participants/{pid}/consents` | Versioned consent (hash of the plain-language text) |
| DELETE | `/v1/participants/{pid}/consents/{cid}` | Withdraw: stops sessions, deletes raw telemetry, keeps only hashes |
| GET/POST | `/v1/devices` | Simulated devices only (`"simulated": true` is required) |
| POST | `/v1/devices/{id}/calibrations` | REPORTED (named instrument) or SYNTHETIC (simulated devices) |
| POST | `/v1/protocols` | Stored only if it validates |
| POST | `/v1/protocols/{id}:validate` | Dry run; returns every violation; never stores |
| POST | `/v1/sessions` | Participant, protocol, devices, optional seed |
| POST | `/v1/sessions/{sid}:arm` | → ARMED or REFUSED |
| POST | `/v1/sessions/{sid}:start` | `{"confirmation": {"input": "switch", "intent": "start"}}` |
| POST | `/v1/sessions/{sid}:stop` | Always 200 |
| POST | `/v1/sessions/{sid}:clear-latch` | Operator clearing (archived). Added; see deviations |
| POST | `/v1/sessions/{sid}/interactions` | `stop` (any input), `dim`, `comfort_rating`, `acknowledge` |
| POST | `/v1/sessions/{sid}/telemetry` | 200 with R and S, or 422 `SAFETY_LATCH` when the frame latches |
| GET | `/v1/sessions/{sid}` | State, latch status, elapsed time, simulated output |
| GET | `/v1/sessions/{sid}/evidence` | Manifest + report (`?format=markdown` for Markdown) |
| WS | `/v1/sessions/{sid}/stream` | Send `{"type": "heartbeat"}` or `{"type": "stop"}`; receives snapshot, events, acks |

Errors are RFC 9457 `application/problem+json`. Envelope refusals carry `"code": "SAFETY_ENVELOPE"`, the failing
`rule` and the full `violations` list. Validation errors never echo the submitted input. See
[docs/problems.md](docs/problems.md).

## Quick start

```bash
pip install ".[serve,test]"
python -m pytest --cov              # coverage gate 95%
python -m spark_iis demo            # rewrites examples/demo/ (seed 42, SYNTHETIC)
spark-iis serve                     # http://127.0.0.1:8000/docs (simulated devices, in-memory store)
```

## Seed-42 synthetic demo (`examples/demo/`)

Everything below is **SYNTHETIC**: simulated devices, a fake clock and generated sensor frames. No person and no
hardware were involved, and nothing here is evidence of any effect on anyone.

| Session | Result (SYNTHETIC) |
|---|---|
| `fade-calm-01` (8 s-period fade to brightness 0.5 + 220 Hz tone at 45 dB(A), 60 s) | `COMPLETED` (`protocol_complete`); 59 frames, 0 dimmed, 0 latched; heartbeats every 200 ms over the WebSocket |
| Same protocol, frame drifted by +0.8 at t = 5000 ms | `LATCHED` (`residual_latch`, S = 0.80 ≥ τ 0.50); then cleared by operator `op-demo` (archived) |
| `flicker-red-7p5` (7.5 Hz saturated-red flicker over 40% of the field, with a "brain coherence" claim) | Refused, 422 `SAFETY_ENVELOPE`: `PHOTOSENSITIVITY_FLASH_RATE`, `RED_SATURATION`, `FIELD_SIZE`, `FADE_ONLY`, `CLAIM_FORBIDDEN`, `CLAIM_UNSUPPORTED`; never stored |

CI regenerates the demo on Python 3.10–3.13 and compares every file byte for byte. The manifest records the package
source-tree hash and the seed; the git commit is recorded when `SPARK_IIS_GIT_COMMIT` is set, otherwise `unrecorded`.

## Privacy

Participants are pseudonymous (`p-` + 12 hex characters). The store is in memory and local; nothing is uploaded.
No wall-clock time is recorded (session-relative monotonic milliseconds only). Withdrawing consent stops the
participant's sessions and deletes raw telemetry, keeping only SHA-256 hashes in the session and the audit log.

## Deviations from and interpretations of the v0.1 spec

- Audio steps must declare `fade_in_s` and `fade_out_s` (≥ 5 s each); the spec's abbreviated example omits them and
  is therefore refused as written.
- Steps run concurrently from their `start_s` (default 0); session length is the latest step end.
- Every flicker/flash pattern is refused (`FADE_ONLY`), not only those above 3 Hz, following "only slow fades are
  allowed"; rate, red and field-size rules are still reported individually.
- Added `POST /v1/sessions/{sid}:clear-latch` for the operator clearing the spec requires. `operator_id` is declared,
  not authenticated.
- Stop drops simulated output to zero immediately rather than ramping (satisfies the 1 s TARGET).
- Stale reference = the sensor's calibration changed after ARM. Missing sensor = no frame for over 1000 ms (TARGET,
  my choice). Dim threshold 0.35 (TARGET, my choice).
- Interaction bodies do not carry `sid`/`t_ms`; the server stamps them.
- Telemetry defects return 422 problem+json with `code: SAFETY_LATCH` (the latch event is included).
- Devices must declare `"simulated": true`; `sound_sensitivity` is recorded but does not block audio.
- The clinician free-text annotation mentioned in the spec is not implemented.
- No authentication and an in-memory store: this is a local reference server, not a deployable service.

## Not in v0.1

- **P2:** accessible demo UI (switch, gaze, captions) — tracked as an issue.
- **Research mode:** out of scope (needs ethics-board approval, a named operator, screening and a separate signed
  build) — tracked as an issue, not planned for v0.1.

## Licence and citation

AGPL-3.0-only (see [LICENSE](LICENSE)); commercial licensing in [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).
Citation metadata: [CITATION.cff](CITATION.cff). Author: Jean-François Brisson, Spark AI NLP
(ORCID [0009-0000-9778-5374](https://orcid.org/0009-0000-9778-5374)).
