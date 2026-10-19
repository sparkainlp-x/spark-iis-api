# Contributing

Thank you for helping. Please read the [Code of Conduct](CODE_OF_CONDUCT.md) first. Report security issues privately
as described in [SECURITY.md](SECURITY.md), not as public issues.

## Ground rules

- **Never loosen a safety lock.** The limits in the README are not configurable in v0.1. Anything outside them is
  research mode, which is out of scope.
- **Stop always works.** No change may make `:stop` fail, or make any path raise intensity from sensor data.
- **Fail closed.** Every model forbids unknown fields; NaN, inf, wrong shapes and missing values are refused or latch.
  Never clip, impute or silently drop them.
- **Simulated only.** No hardware drivers and no medical-device or HBOT integration.
- **No health claims.** No therapeutic, neurological, "coherence", HBOT-synergy or quantum claim, anywhere.
- **No personal data.** Never commit real participant data, names, contact details or health records.
- **Tag every number** as SYNTHETIC, REPORTED (measured on named real data, with provenance), TARGET or UNRUN.
- **Licence.** AGPL-3.0-only (see [LICENSE](LICENSE) and [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)). By
  contributing, you agree that your contribution is licensed under the same terms.

## Before you open a pull request

1. Run `ruff check .` and `python -m pytest --cov` (coverage of at least 95%; CI runs Python 3.10–3.13).
2. If the demo output changes, run `python -m spark_iis demo` and commit the regenerated `examples/demo/` files.
   Explain in the PR why they changed.
3. Update the README and `CHANGELOG.md` for any user-visible change.
