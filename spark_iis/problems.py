# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""RFC 9457 problem details. Envelope refusals carry ``"code": "SAFETY_ENVELOPE"`` and the exact rule."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

PROBLEM_MEDIA_TYPE = "application/problem+json"
TYPE_BASE = "https://github.com/sparkainlp-x/spark-iis-api/blob/main/docs/problems.md#"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str,
        *,
        rule: str | None = None,
        violations: Sequence[dict[str, Any]] = (),
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status, self.code, self.title, self.detail = status, code, title, detail
        self.rule = rule if rule is not None else (violations[0]["rule"] if violations else None)
        self.violations = list(violations)
        self.extra = extra or {}

    def body(self, instance: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": TYPE_BASE + self.code.lower().replace("_", "-"),
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
            "code": self.code,
        }
        if self.rule is not None:
            body["rule"] = self.rule
        if self.violations:
            body["violations"] = self.violations
        body.update(self.extra)
        return body


def envelope_refusal(detail: str, violations: Sequence[dict[str, Any]], **extra: Any) -> ProblemError:
    return ProblemError(422, "SAFETY_ENVELOPE", "Refused by the safety envelope", detail,
                        violations=violations, extra=extra)


def not_found(what: str) -> ProblemError:
    return ProblemError(404, "NOT_FOUND", "Not found", f"{what} does not exist")


def conflict(detail: str, **extra: Any) -> ProblemError:
    return ProblemError(409, "SESSION_STATE", "Not allowed in the current state", detail, extra=extra)
