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
- deterministic software analytics driven by trusted PostgreSQL column metadata;
- governed metric summaries, dimension rankings, and temporal series;
- deterministic KPI, bar, and line visualization specifications;
- stable chart identifiers and fixed ordering without LLM chart selection;
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

- grounded insight generation;
- Streamlit visualization rendering, evaluation, observability, and CI/CD.

Features are marked as implemented only after code execution, real output,
tests, and explicit validation.

## Core principle

> The LLM proposes. The software validates. The database restricts.
> The code calculates. The AI explains.

## Validated pipeline

Natural-language question → schema grounding → semantic context → controlled
SQL proposal → SQLGlot AST and security validation → bounded repair → validated
read-only SQL → least-privilege PostgreSQL execution → trusted result metadata →
deterministic software analytics → typed KPI, bar, and line visualization
specifications.

Future phases will add grounded explanations, interactive Plotly rendering, and
analytical presentation through Streamlit.

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
timeouts. Deterministic analytics accepts only trusted internal result metadata
and never delegates calculations to the LLM. Deterministic visualization accepts
only internal analytics results and has no LLM, database, network, or rendering
capability. Future phases will add audit events and frontend visualization
controls.

## Current API surface

- `GET /health` provides process liveness and service metadata;
- `GET /ready` validates both PostgreSQL pools and transaction modes;
- `GET /api/v1/schema/catalog` returns the safe SQL schema catalog;
- `POST /api/v1/grounding/context` builds safe question context;
- `POST /api/v1/sql/generate` returns validated read-only SQL without executing
  it;
- `POST /api/v1/query/execute` generates, validates, and executes one controlled
  read-only query;
- `POST /api/v1/analytics/analyze` generates, executes, and deterministically
  analyzes one governed natural-language question;
- `POST /api/v1/visualizations/specify` generates deterministic KPI, bar, and
  line specifications for one governed natural-language question;
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

## Deterministic analytics

The application owns a stateless deterministic analytics engine through the
FastAPI lifespan. SQL generation carries its compact grounding context as
excluded internal metadata, while query execution obtains trusted PostgreSQL
type codes from the cursor description. Neither internal context nor column
metadata appears in serialized responses or OpenAPI.

`POST /api/v1/analytics/analyze` accepts only a natural-language question.
Clients cannot submit raw SQL, result rows, or previously generated execution
objects. The server performs grounding, generation, AST validation, bounded
repair, read-only execution, and deterministic analysis in that order.

The engine uses typed result metadata and fixed-scale `Decimal` arithmetic.
It calculates metric totals, averages, minima, and maxima; dimension rankings
and shares; and ordered temporal series with absolute and percentage changes.
Numeric-looking strings are never guessed as numbers. Missing or unknown
metadata, non-finite values, inconsistent results, and unsupported analytical
shapes fail closed.

The LLM proposes SQL but never calculates analytical outputs. Analytics
responses expose governed summaries, rankings, series, source-row counts, and
version metadata without returning raw SQL or database rows. The endpoint
returns HTTP 200 for deterministic results, HTTP 422 for invalid or unsafe
requests, and HTTP 503 for unavailable managed services. Responses disable
client caching and expose controlled analytics, execution, catalog, and
semantic versions.

Manual validation exercised the endpoint through a real Uvicorn HTTP server
and the local PostgreSQL analytics role. A governed approved-revenue-by-region
request analyzed five rows, matched the database total, preserved descending
ranking order, and left region, order, and payment counts unchanged. Runtime
read-only checks remained enabled, the deterministic provider generated one
SQL proposal, graceful shutdown closed the provider, and no external network
request was made.

## Deterministic visualization specifications

The application owns a stateless deterministic visualization engine through the
FastAPI lifespan. The engine accepts only the immutable deterministic analytics
result produced internally. Clients cannot submit SQL, query rows, execution
objects, or analytics objects.

`POST /api/v1/visualizations/specify` accepts only a natural-language question.
The server performs grounding, SQL generation, AST validation, bounded repair,
read-only execution, deterministic analysis, and visualization specification in
that order.

Each governed metric summary produces one KPI specification, each categorical
ranking produces one bar specification, and each temporal series produces one
line specification. Metric units and fixed-scale `Decimal` values are preserved.
Stable SHA-256 identifiers and the fixed KPI, bar, then line order make results
reproducible.

The visualization engine does not call an LLM, query the database, access the
network, import Plotly, or render charts. Responses contain typed specifications
and version metadata without raw SQL, rows, grounding context, or internal
column metadata. The endpoint returns HTTP 200 for deterministic specifications,
HTTP 422 for invalid or unsafe requests, and HTTP 503 for unavailable managed
services. Responses disable client caching.

Manual validation exercised the endpoint through a real Uvicorn HTTP server and
the local PostgreSQL analytics role. A governed approved-revenue-by-region
request generated and executed one schema-qualified read-only query. The KPI and
five-item regional bar matched the database total, preserved descending ranking
order, and left region, order, and payment counts unchanged. Runtime read-only
checks remained enabled, only one provider generation occurred, and a canonical
restricted request was blocked before the provider. Graceful shutdown closed
the provider, released port 8000, and made no external network request.

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

- 349 automated tests passed;
- branch-aware backend coverage is 90.41%;
- Ruff lint and format checks pass;
- mypy strict mode passes;
- no known dependency vulnerabilities were found;
- no local secret values were detected in versionable source files.

## Development protocol

A gate passes only with:

> Code + manual execution + real evidence + agent validation.
