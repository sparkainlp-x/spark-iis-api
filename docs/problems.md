# Problem types

All errors are RFC 9457 `application/problem+json` with `type`, `title`, `status`, `detail`, `instance` and `code`.

## safety-envelope

`code: SAFETY_ENVELOPE`, status 422. The request was refused by the safety envelope or by fail-closed validation.
`rule` is the first failing rule and `violations` lists every violation (`rule`, `path`, `message`, `value`, `limit`).
Validation errors use `rule: FAIL_CLOSED_VALIDATION` and an `errors` list of `loc`/`type`/`msg`; the submitted input
is never echoed.

## safety-latch

`code: SAFETY_LATCH`, status 422. A telemetry frame latched the session (`rule` is the latch reason, `latch` is the
latch event). The session is now `LATCHED` and output is zero.

## session-state

`code: SESSION_STATE`, status 409. The action is not allowed in the session's current state. (Stop is never refused.)

## not-found

`code: NOT_FOUND`, status 404.

## conflict

`code: CONFLICT`, status 409. A device or protocol with that ID already exists.

## method-not-allowed

`code: METHOD_NOT_ALLOWED`, status 405.

## internal

`code: INTERNAL`, status 500. Output is zero.
