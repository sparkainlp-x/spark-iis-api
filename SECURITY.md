# Security policy

spark-iis-api is a local reference server with **simulated** devices only. It drives no hardware, stores data in
memory only, uploads nothing and has **no authentication**. Do not expose it to a network you do not control, and do
not connect it to real displays, audio, haptic devices or sensors.

It is **not** a medical device. It must never be connected to a hyperbaric chamber, an oxygen supply or any medical
device.

## What counts as a security issue

Besides the usual software vulnerabilities, please treat these as security issues and report them privately:

- any way to make output **start, continue or increase** when the envelope, a latch, a heartbeat timeout or a stop
  should prevent it;
- any way to make `:stop` fail or return anything other than success;
- any protocol, device or frame that bypasses the safety envelope or the latch (for example an unknown field that is
  accepted, or a NaN that passes);
- any way to store or leak a name, contact detail or health record.

## Supported versions

Only the `main` branch receives fixes.

## Reporting a vulnerability

Please **do not open a public issue**. Contact the maintainer, Jean-François Brisson, privately via the contact
details on the GitHub profile (https://github.com/sparkainlp-x) or the website (https://sparkainlpx.xyz). Include the
affected commit, steps to reproduce and the impact you expect. You should get an acknowledgement within 7 days.
