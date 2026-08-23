# AI SQL Data Analyst

AI-powered analytics platform designed to transform natural-language business
questions into secure SQL queries, deterministic analytics, grounded
explanations, and interactive visualization specifications.

## Project status

The project is under active, evidence-based implementation.

Implemented and manually validated:

- FastAPI application factory and `/health` endpoint;
- typed environment configuration with protected secrets;
- PostgreSQL 18.6 through Docker Compose;
- Alembic migration infrastructure;
- normalized retail schema with eight tables;
- least-privilege PostgreSQL runtime roles;
- column-level protection for sensitive customer fields;
- deterministic synthetic retail dataset;
- transactional bulk loading with PostgreSQL `COPY`;
- duplicate-load protection and rollback guarantees;
- automated linting, typing, tests, coverage, and dependency auditing.

Still planned:

- schema intelligence and semantic layer;
- LLM provider abstraction;
- Text-to-SQL generation, validation, and repair;
- deterministic analytics and visualization engines;
- grounded insight generation;
- Streamlit, evaluation, observability, and CI/CD.

Features are marked as implemented only after code execution, real output,
tests, and explicit validation.

## Core principle

> The LLM proposes. The software validates. The database restricts.
> The code calculates. The AI explains.

## Planned pipeline

Natural-language question → schema grounding → semantic context → SQL proposal
→ AST and security validation → read-only PostgreSQL execution → deterministic
analytics → chart specification → grounded explanation.

## Security foundations

Currently validated:

- least-privilege PostgreSQL roles;
- read-only analytics transactions;
- restricted customer columns;
- localhost-only database binding;
- statement and lock timeouts;
- blocked runtime writes and DDL.

Future application phases will add SQLGlot AST validation, table allowlists,
prompt-injection controls, repair revalidation, audit events, and result
limits.

## Validated database foundation

The `retail` schema contains:

- 5 regions;
- 12 categories;
- 5,000 synthetic customers;
- 1,000 products;
- 50,000 orders;
- 157,765 order items;
- 50,000 payments;
- 280 monthly regional sales targets.

The dataset contains 264,062 rows covering January 2022 through August 2026.
It occupies 42.62 MB in the validated environment.

Security validation proved that `email` and `document_number` cannot be read
by the analytics role. INSERT, UPDATE, DELETE, DROP, and `SELECT *` on
customers are also blocked.

## Quality evidence

- 83 automated tests passed;
- branch-aware backend coverage is 85.33%;
- Ruff lint and format checks pass;
- mypy strict mode passes;
- no known dependency vulnerabilities were found;
- no local secret values were detected in versionable source files.

## Development protocol

A gate passes only with:

> Code + manual execution + real evidence + agent validation.
