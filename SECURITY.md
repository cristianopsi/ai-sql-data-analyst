# Security policy

## Supported versions

Security fixes are applied to the current `0.1.x` development line. Older
revisions are not maintained as separate supported releases.

## Reporting a vulnerability

Do not publish credentials, private data, exploit payloads, or an
undisclosed vulnerability in a public issue.

Prefer GitHub private vulnerability reporting when it is enabled for the
repository. Otherwise, contact the maintainer through the GitHub profile
associated with this repository and request a private reporting channel.
Include the affected component, reproducible conditions, expected impact,
and a minimal sanitized demonstration.

## Credential handling

- Never commit `.env`, API keys, database passwords, tokens, or provider
  credentials.
- Start from `.env.example` and keep real values only in the local
  environment or an approved secret manager.
- Do not pass credentials through Docker build arguments.
- Do not paste credential values into issues, pull requests, logs, or test
  fixtures.
- Rotate any credential before external publication if it may have been
  exposed during development.

## Security boundaries

The application uses multiple independent controls:

- only supported `SELECT` and CTE statements pass SQL validation;
- referenced tables and columns are checked against grounded metadata;
- the analytics database role is read-only;
- query timeout and row limits constrain execution;
- public requests accept a question rather than client-supplied SQL;
- backend and frontend containers run as UID `10001`;
- published ports bind to loopback by default;
- CI smoke containers run without network access;
- provider credentials are runtime configuration and are not baked into
  images.

These controls reduce risk but do not replace deployment-specific access
control, TLS, network policy, rate limiting, monitoring, backup, and secret
management.

## Scope

Reports concerning SQL validation bypasses, authorization boundaries,
credential exposure, unsafe provider output, container privilege,
dependency vulnerabilities, or unintended database writes are considered
security-relevant.
