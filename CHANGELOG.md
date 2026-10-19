# Changelog

## 0.1.0 (unreleased)

- First reference server for the Spark IIS API v0.1 (P0 + P1): every `/v1` endpoint, the session state machine,
  the hard safety envelope, always-succeeding idempotent stop, residual latch on 32-value telemetry blocks,
  WebSocket heartbeat stream, consent withdrawal, RFC 9457 problem+json errors, provenance manifest and
  JSON/Markdown evidence report.
- Seed-42 SYNTHETIC demo in `examples/demo/`, reproduced byte for byte in CI.
- pytest + Hypothesis property tests; coverage gate 95%.
- Simulated devices only; no hardware drivers; not a medical device.
