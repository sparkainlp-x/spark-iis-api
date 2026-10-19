# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""The boundary statement. It goes in every README, response header and report (spec, top)."""

BOUNDARY_STATEMENT = (
    "This software is not a medical device. It does not diagnose, treat, cure or prevent any condition. "
    'No therapeutic, neurological, "coherence", HBOT-synergy or quantum effect is claimed or established. '
    "All numbers are tagged SYNTHETIC, REPORTED, TARGET or UNRUN."
)
BOUNDARY_HEADER = "X-Spark-Boundary"
EVIDENCE_TAGS = ("SYNTHETIC", "REPORTED", "TARGET", "UNRUN")
