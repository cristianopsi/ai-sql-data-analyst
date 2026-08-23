# AI SQL Data Analyst

AI-powered analytics platform designed to transform natural-language business
questions into secure SQL queries, deterministic analytics, grounded
explanations, and interactive visualization specifications.

## Project status

The project is under active, evidence-based implementation.

Implemented and manually validated:

- FastAPI application factory with `/health` liveness and `/ready` readiness;
- independent application and analytics PostgreSQL connection pools;
- managed pool startup and shutdown through the FastAPI lifespan;
- secure schema intelligence generated from SQLAlchemy metadata;
- thread-safe schema catalog caching with TTL and ETag revalidation;
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

- semantic business layer and question-to-schema grounding;
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

## Current API surface

- `GET /health` provides process liveness and service metadata;
- `GET /ready` validates both PostgreSQL pools and transaction modes;
- `GET /api/v1/schema/catalog` returns the safe SQL schema catalog;
- `/docs` and `/redoc` expose interactive API documentation;
- `/openapi.json` provides the machine-readable API contract.

The API binds to localhost in the validated development environment. Database
pools are created closed, opened during application startup, and closed during
graceful shutdown. Readiness failures return HTTP 503 without exposing
connection strings, credentials, or internal exceptions.

## Safe schema intelligence

The application builds a deterministic catalog from the declared SQLAlchemy
metadata without querying the live database. Catalog version 1 exposes eight
retail tables, 52 permitted columns, and eight foreign-key references.

The catalog includes documented data types, nullability, primary keys, and
relationships. Its exposure policy is derived from the PostgreSQL grant policy,
so `customers.email` and `customers.document_number` are never included.

A thread-safe in-memory cache builds the catalog lazily and uses the configured
300-second TTL. The HTTP endpoint supplies an ETag, supports conditional
requests with HTTP 304, and returns a sanitized HTTP 503 response if catalog
construction is unavailable.

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

- 117 automated tests passed;
- branch-aware backend coverage is 88.72%;
- Ruff lint and format checks pass;
- mypy strict mode passes;
- no known dependency vulnerabilities were found;
- no local secret values were detected in versionable source files.

## Development protocol

A gate passes only with:

> Code + manual execution + real evidence + agent validation.
