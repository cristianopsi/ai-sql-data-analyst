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
- deterministic semantic layer with governed metrics and dimensions;
- safe question grounding with restricted-intent blocking;
- compact grounding context exposed through a controlled API;
- managed LLM provider abstraction with deterministic mock and
  OpenAI-compatible adapters;
- controlled Text-to-SQL proposals built only from compact grounded context;
- SQLGlot AST validation with contextual table, column, and function
  allowlists;
- bounded SQL repair with revalidation after every proposal;
- validated SQL generation API that does not execute generated statements;
- controlled read-only query execution through the least-privilege analytics
  role;
- strict JSON-safe query result contracts with enforced row and timeout
  metadata;
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

- deterministic analytics and visualization engines;
- grounded insight generation;
- Streamlit, evaluation, observability, and CI/CD.

Features are marked as implemented only after code execution, real output,
tests, and explicit validation.

## Core principle

> The LLM proposes. The software validates. The database restricts.
> The code calculates. The AI explains.

## Validated pipeline

Natural-language question → schema grounding → semantic context → controlled
SQL proposal → SQLGlot AST and security validation → bounded repair → validated
read-only SQL → least-privilege PostgreSQL execution.

Future phases will add deterministic analytics, chart specifications, and
grounded explanations.

## Security foundations

Currently validated:

- least-privilege PostgreSQL roles;
- read-only analytics transactions;
- restricted customer columns;
- localhost-only database binding;
- statement and lock timeouts;
- blocked runtime writes and DDL.

Application-layer controls enforce SQLGlot AST validation, contextual table
and column allowlists, function allowlists, bounded repair revalidation, and
mandatory result limits. Execution uses only the analytics pool, explicitly
enters a read-only transaction, verifies its runtime mode, and applies controlled
timeouts. Future phases will add audit events and deterministic analytics.

## Current API surface

- `GET /health` provides process liveness and service metadata;
- `GET /ready` validates both PostgreSQL pools and transaction modes;
- `GET /api/v1/schema/catalog` returns the safe SQL schema catalog;
- `POST /api/v1/grounding/context` builds safe question context;
- `POST /api/v1/sql/generate` returns validated read-only SQL without executing
  it;
- `POST /api/v1/query/execute` generates, validates, and executes one controlled
  read-only query;
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

## Deterministic semantic grounding

Semantic version 1 defines 14 dimensions, nine governed metrics, eight
relationships, five business rules, and 22 curated vocabulary values. Every
semantic reference is validated against the safe schema catalog, preventing
restricted customer columns from entering downstream context.

Question grounding normalizes user input and deterministically selects metrics,
dimensions, vocabulary values, tables, relationship paths, and applicable
business rules. Results use the explicit statuses `grounded`, `ambiguous`,
`unsupported`, and `restricted`.

The grounding service creates compact context containing only the safe objects
required for the question. Serialized context is limited to 20,000 characters
and fails closed instead of truncating its contract. Restricted and unsupported
questions receive no schema context.

`POST /api/v1/grounding/context` returns grounded context with HTTP 200,
restricted requests with HTTP 403, invalid or unsupported requests with HTTP
422, and sanitized service failures with HTTP 503. Responses disable client
caching and expose catalog, semantic, and grounding versions through controlled
headers.

Grounding itself does not generate SQL, call an LLM, or query the database.

## Controlled LLM and Text-to-SQL pipeline

The application owns a typed LLM provider through the FastAPI lifespan and
closes it during graceful shutdown or failed startup. A deterministic mock
provider supports local development and testing. The OpenAI-compatible HTTP
adapter supports OpenAI, Gemini, Groq, OpenRouter, and local Ollama
configurations without requiring provider-specific SDK packages.

External provider URLs require HTTPS. Plain HTTP Ollama URLs are accepted only
for loopback hosts. Provider responses have controlled size, content type, JSON
shape, completion status, and error handling. API keys remain protected by the
typed settings contract and are never included in sanitized failures.

Text-to-SQL generation sends only compact grounded context to the configured
provider. The provider must return a strict JSON proposal containing SQL and an
explanation. Every proposal remains explicitly unvalidated until it passes the
separate SQLGlot security validator.

The validator accepts exactly one PostgreSQL `SELECT` statement and verifies
every referenced table and column against the question context. It also applies
a function allowlist and rejects multiple statements, writes, DDL, `COPY`,
stars, comments, locks, `SELECT INTO`, common table expressions, subqueries,
and set operations. A mandatory row limit is added or reduced to the configured
maximum.

Rejected proposals may enter a bounded repair loop. Every repaired proposal
passes through the complete validator again, and exhaustion fails with a
sanitized error instead of returning unsafe SQL.

`POST /api/v1/sql/generate` returns validated SQL with HTTP 200, unsafe or
unsupported requests with HTTP 422, and unavailable services with HTTP 503.
Responses disable client caching and expose controlled generation, validation,
catalog, and semantic version headers.

`POST /api/v1/sql/generate` stops after validation and never executes its
result. It remains available when a caller needs to inspect validated SQL
without accessing the database.

## Controlled read-only query execution

The application owns a query executor through the FastAPI lifespan. Its
analytics-pool dependency is resolved lazily, allowing safe startup validation
before any query is requested. Failed startup closes the managed LLM provider
and does not open partially constructed database resources.

`POST /api/v1/query/execute` accepts only a natural-language question. Clients
cannot submit raw SQL or claim that a statement was previously validated. The
server internally performs grounding, proposal generation, AST validation,
bounded repair, and execution in that order.

The executor runs exactly the SQL returned by the validator and uses only the
least-privilege analytics pool. Every execution explicitly issues
`SET TRANSACTION READ ONLY`, applies the configured PostgreSQL statement
timeout, verifies `transaction_read_only` at runtime, and uses the configured
pool acquisition timeout. Failure of any invariant returns a sanitized
response.

Database values are normalized into strict JSON-safe primitives. Finite decimal
values, dates, times, and UUIDs receive deterministic string representations.
Duplicate or empty columns, inconsistent row widths, unsupported values,
non-finite numbers, and results above the validated row limit fail closed.

The query-execution endpoint returns HTTP 200 for an executed result, HTTP 422
for invalid questions or unsafe generation and execution, and HTTP 503 for
unavailable managed services. Responses disable client caching and expose only
controlled execution, generation, validation, catalog, and semantic version
headers.

Manual validation exercised the endpoint through a real Uvicorn HTTP server and
the local PostgreSQL analytics role. A governed revenue-by-region query returned
five rows under a 25-row limit with an 8,000-millisecond statement timeout.
Runtime read-only checks remained enabled, database row counts were unchanged,
and no external provider request was made.

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

- 280 automated tests passed;
- branch-aware backend coverage is 91.42%;
- Ruff lint and format checks pass;
- mypy strict mode passes;
- no known dependency vulnerabilities were found;
- no local secret values were detected in versionable source files.

## Development protocol

A gate passes only with:

> Code + manual execution + real evidence + agent validation.
