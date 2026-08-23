# AI SQL Data Analyst

AI-powered platform for converting natural-language business questions into
secure SQL queries, deterministic analytics, grounded explanations, and
visualization specifications.

## Status

The project is under active, evidence-based implementation.

Validated:

- Debian 13 on WSL 2;
- Python 3.13 isolated in `.venv`;
- Git, Docker Engine, and Docker Compose;
- initial Python package and project configuration.

Planned application features are not considered implemented until they have
code, manual execution, real output, tests, and validation.

## Core principle

> The LLM proposes. The software validates. The database restricts.
> The code calculates. The AI explains.

## Planned pipeline

Natural-language question → schema grounding → semantic context → SQL proposal
→ AST and security validation → read-only PostgreSQL execution → deterministic
analytics → chart specification → grounded explanation.

## Security foundations

- SQL parsing with SQLGlot;
- statement and table allowlists;
- restricted-column policies;
- read-only database role and transactions;
- statement timeout and result limits;
- mandatory revalidation after SQL repair;
- structured logging and audit events;
- prompt-injection and unsafe-SQL tests.

## Development protocol

A gate passes only with:

> Code + manual execution + real evidence + agent validation.
