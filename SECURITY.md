# Security

Chrolo is experimental. There is no published supported-release or security
response-time commitment yet. Security fixes are developed on the current
development branch.

## Reporting a vulnerability

Keep exploit details, private traces, credentials, and personal information
out of public issues and pull requests.

Use a maintainer's published private security contact or the repository's
private vulnerability reporting feature if it is enabled. This checkout does
not specify a verified private contact. If no confidential route is available,
open a minimal public issue requesting a private reporting channel, without
including the vulnerability details or affected data.

A private report should describe the affected revision, expected boundary,
observed behavior, impact, and a small synthetic reproduction. Do not send
production recordings or live credentials. Acknowledgment and disclosure timing
must be agreed with a maintainer; no response-time guarantee is implied.

## Execution and data boundaries

- Factories, runners, failure oracles, and generated tests import and execute
  Python. Use trusted code and inspect unfamiliar fixtures before execution.
- Cassette replay covers explicitly wrapped calls. It does not sandbox graph
  code or intercept arbitrary network, database, or filesystem operations.
- Recording runs callbacks and may perform real external operations. Captures
  and minimized fixtures can contain confidential requests and responses.
- Secret scanning is heuristic. Redaction and filtering produce non-replayable
  review copies; they do not establish that all sensitive data was removed.
- The Trace Desk defaults to loopback. Remote use requires a trusted network,
  authentication, and appropriate TLS configuration.

The certificate and private key under `tests/fixtures/` are intentionally public
localhost-only test data. They must never be used for a real deployment.

See the [replay privacy review](docs/replay-privacy-review.md) for verified
diagnostic behavior and the [integration guide](docs/replay-guide.md) for scope.
