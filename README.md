# AI SQL Data Analyst

[![CI](https://github.com/cristianopsi/ai-sql-data-analyst/actions/workflows/ci.yml/badge.svg)](https://github.com/cristianopsi/ai-sql-data-analyst/actions/workflows/ci.yml)

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
- deterministic KPI, table, bar, and line visualization specifications;
- bounded categories, rows, and points with stable ordering and identifiers;
- grounded narrative insights supported by allowlisted internal evidence;
- fail-closed claim validation without LLM calculation or chart selection;
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

## Core principle

> The LLM proposes. The software validates. The database restricts.
> The code calculates. The AI explains.

## Validated pipeline

Natural-language question → schema grounding → semantic context → controlled
SQL proposal → SQLGlot AST and security validation → bounded repair → validated
read-only SQL → least-privilege PostgreSQL execution → trusted result metadata →
deterministic software analytics → typed KPI, table, bar, and line visualization
specifications → grounded narrative insights with validated evidence references.

Streamlit renders validated table and Plotly specifications without
recalculating metrics or accepting executable chart instructions.

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
capability. Grounded insight generation accepts only internal analytics and
visualization evidence. The LLM writes narrative but cannot calculate metrics,
generate SQL, or select charts; software validates every claim and evidence
reference. Future phases will add structured audit events and operational
metrics.

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
- `POST /api/v1/visualizations/specify` generates deterministic KPI, table,
  bar, and line specifications for one governed natural-language question;
- `POST /api/v1/insights/generate` produces grounded narrative claims for one
  governed natural-language question;
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
function, table, and column allowlists; rejects multiple statements, writes,
DDL, `COPY`, comments, locks, `SELECT INTO`, subqueries, set operations, and
uncontrolled star expressions; and validates joins against grounded semantic
relationships. `COUNT(*)` is accepted only for controlled row counts. Queries
may use at most two independent, non-recursive top-level CTEs with explicit
named outputs and physical-column lineage. Recursive, nested, data-modifying,
dependent, shadowing, materialized, set-operation, offset, or internally
limited CTEs fail closed. A mandatory outer row limit is added or reduced to
the configured maximum.

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
least-privilege analytics pool. Every execution explicitly enters a read-only
transaction and applies transaction-local PostgreSQL statement, lock, and
idle-in-transaction session timeouts. Before generated SQL runs, the executor
confirms `transaction_read_only` and verifies all three active timeout values.
Pool acquisition uses a separately configured timeout. Failure of any invariant
returns a sanitized response.

Rows are read with `fetchmany` in a batch capped at the validated row limit plus
one, allowing overflow to fail closed without an unbounded `fetchall`. Database
values are normalized into strict JSON-safe primitives. Finite decimal values,
dates, times, and UUIDs receive deterministic string representations. Duplicate
or empty columns, inconsistent row widths, unsupported or non-finite values,
and results above the validated row limit fail closed. Normalization and
complete result-contract validation occur inside the transaction, so failures
roll back before any result is returned.

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

The engine uses trusted typed result metadata and verifies that execution,
semantic-context, and catalog versions match before calculation. Numeric source
values retain exact `Decimal` precision through aggregation and are quantized to
the public four-decimal scale only at controlled contract boundaries. Governed
metric aggregation selects the primary value used by downstream visualization
and evaluation.

Temporal labels are parsed under their declared day, month, quarter, or year
granularity and ordered chronologically. Metric summaries, ranking shares,
series variations, deterministic ordering, and source-row counts are
cross-validated by immutable result contracts. Any provenance, temporal,
numeric, or arithmetic inconsistency fails closed.
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

Each governed metric summary produces one KPI specification. Each categorical
ranking produces a bounded table and bar specification, and each temporal
series produces one bounded line specification. Tables are limited to 100 rows,
bars to 20 categories, and lines to 200 points. Selection and truncation are
deterministic, while metric units and fixed-scale `Decimal` values are
preserved. Stable SHA-256 identifiers and the fixed KPI, table, bar, then line
order make results reproducible.

Visualization specifications use canonical SHA-256 specification identifiers
derived from their chart type and governed semantic identifiers. Every result
requires a positive source row count and rejects duplicate semantic
specifications. KPI specifications serialize the governed aggregation and total,
validate the average against the total and value count, and enforce the
aggregation-specific primary value.

Table and bar specifications preserve the full ranking total and validate every
displayed share against it. Paired table and bar specifications for the same
metric and dimension must remain consistent. Line specifications validate each
previous value and its controlled absolute and percentage changes.

The visualization engine does not call an LLM, query the database, access the
network, import Plotly, or render charts. Responses contain typed specifications
and version metadata without raw SQL, rows, grounding context, or internal
column metadata. The endpoint returns HTTP 200 for deterministic specifications,
HTTP 422 for invalid or unsafe requests, and HTTP 503 for unavailable managed
services or unexpected runtime failures. Unexpected failures return sanitized
JSON without internal exception details. Responses disable client caching with
`Cache-Control: no-store`.

Manual validation exercised the endpoint through a real Uvicorn HTTP server and
the local PostgreSQL analytics role. A governed approved-revenue-by-region
request generated and executed one schema-qualified read-only query. The KPI,
bounded regional table, and five-item regional bar matched the database total,
preserved descending ranking order, and left region, order, and payment counts
unchanged. Runtime read-only
checks remained enabled, only one provider generation occurred, and a canonical
restricted request was blocked before the provider. Graceful shutdown closed
the provider, released port 8000, and made no external network request.

## Grounded insight generation

The application owns a grounded insight engine through the FastAPI lifespan and
reuses the managed LLM provider. The engine accepts only immutable deterministic
analytics and visualization results produced internally. Clients cannot submit
SQL, query rows, analytics objects, visualization objects, or evidence packets.

`POST /api/v1/insights/generate` accepts only a natural-language question. The
server performs grounding, SQL generation, AST validation, bounded repair,
read-only execution, deterministic analysis, visualization specification, and
grounded explanation in that order.

The provider receives a bounded evidence packet containing only trusted analytics
and visualization data plus allowlisted metric names and specification
identifiers. Each claim must reference a known metric or visualization. The LLM
proposes only narrative text; it does not calculate metrics, generate additional
SQL, modify deterministic results, or select charts.

Provider JSON is parsed with duplicate-key rejection before strict schema
validation. Software rejects malformed or incomplete responses, unknown or
duplicate evidence references, uncited numeric claims including shorthand
decimals, prohibited SQL material across whitespace variations, and mismatched
source versions or row counts.

Grounded results require a positive source row count and canonical SHA-256 claim
identifiers derived from claim text and an order-independent evidence set.
Semantically duplicate claims are rejected even when evidence order differs.
Returned provider and model identities must exactly match the configured managed
provider.

Qualitative narrative without numeric literals remains provider-authored and is
not independently truth-scored. Deterministic enforcement covers provenance,
identity, evidence references, numeric fidelity, prohibited material, structure,
and bounded output.

Successful responses preserve source versions, provider and model metadata,
token usage, source row count, a bounded summary, grounded claims, and explicit
evidence references. The result declares `grounded=true` and
`calculated_by_llm=false`. Raw SQL, rows, grounding context, column metadata,
evidence packets, and raw provider content are not returned.

The endpoint returns HTTP 200 for grounded results, HTTP 422 for invalid or unsafe
requests, and HTTP 503 for unavailable managed services. Responses disable client
caching and expose only controlled version headers.

Manual validation exercised the endpoint through a real Uvicorn HTTP server and
the local PostgreSQL analytics role. A governed approved-revenue-by-region
request generated one schema-qualified read-only query and one grounded narrative
proposal. Five source rows produced one cited claim, while runtime read-only
checks remained enabled and region, order, and payment counts remained
unchanged. A request containing client evidence was rejected before the provider.
Graceful shutdown closed the shared provider, released port 8000, and made no
external network request.

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

## Systematic evaluation

The project includes a deterministic, versioned evaluation framework for the
complete analytical pipeline. Its packaged reference dataset is version
**1.1.0** and contains ten controlled cases: four valid, two ambiguous, two
restricted, and two out-of-domain requests.

The runner calculates six metrics in a fixed order:

1. `grounding_accuracy`;
2. `sql_validation_rate`;
3. `repair_success_rate`;
4. `unsafe_block_rate`;
5. `calculation_consistency`;
6. `insight_fidelity`.

Each metric exposes numerator, denominator, rate, threshold, and pass/fail
status. A zero denominator fails closed, and one failed threshold fails the
complete report.

Validate the packaged dataset offline:

```bash
.venv/bin/ai-sql-evaluate
```

This default mode does not start FastAPI, access PostgreSQL, call an LLM, or
use the network. Real pipeline evaluation requires explicit opt-in:

```bash
.venv/bin/ai-sql-evaluate --run-runtime
```

The runtime command uses the components published by the FastAPI lifespan and
emits a sanitized JSON report. Questions, SQL, result rows, claim text, and
credentials remain outside the report. See
[`docs/evaluation.md`](docs/evaluation.md) for the complete contract.

## Quality evidence

- 680 automated tests passed;
- branch-aware backend coverage is 88.21%;
- Ruff lint and format checks pass;
- mypy strict mode passes;
- no known dependency vulnerabilities were found;
- no local secret values were detected in versionable source files.

## Development protocol

A gate passes only with:

> Code + manual execution + real evidence + agent validation.

## Architecture and operations

The application follows a defense-in-depth request flow:

1. a user submits a natural-language analytical question;
2. schema intelligence and the semantic layer ground the request;
3. the configured LLM provider proposes SQL;
4. deterministic validation accepts only supported read-only SQL;
5. PostgreSQL enforces a read-only role, timeout, and restricted access;
6. Python calculates analytics and visualization specifications;
7. grounded insights reference evidence produced by the application.

Detailed component boundaries and the complete request flow are documented
in [docs/architecture.md](docs/architecture.md). Installation, Docker
Compose execution, environment variables, troubleshooting, and limitations
are documented in [docs/operations.md](docs/operations.md).

## Requirements and local setup

Supported runtimes are **Python 3.12 and Python 3.13**. PostgreSQL and
Docker with Docker Compose are required for the complete local stack.

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Keep `.env` local and populate its required values without committing or
printing credentials. The repository contains only `.env.example`.

## Docker Compose execution

Validate the configuration before starting services:

```bash
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

The local services are:

- PostgreSQL on the loopback database port configured in `.env`;
- FastAPI at `http://127.0.0.1:8000`;
- Streamlit at `http://127.0.0.1:8501`.

Readiness can be checked without executing an analytical query:

```bash
curl --fail http://127.0.0.1:8000/ready
curl --fail http://127.0.0.1:8501/_stcore/health
```

The complete presentation endpoint is
`POST /api/v1/presentations/generate`. Client-supplied SQL is not accepted
by this public boundary.

## Testing and continuous integration

Run the same quality gates used by GitHub Actions:

```bash
python -m pip check
python -m ruff check .
python -m ruff format --check .
python -m mypy backend frontend tests/unit/test_containerization.py tests/unit/test_ci_configuration.py
python -m pytest -q
```

The validated local baseline contains **680 passing tests**. The CI matrix
covers Python 3.12 and Python 3.13. Its container job validates Compose,
builds both images, and runs non-root smoke tests with `--network none`.

## Security, limitations, and support

The security model combines AST validation, allowlists, query limits,
timeouts, a read-only database role, non-root containers, loopback port
bindings, and controlled provider configuration. See
[SECURITY.md](SECURITY.md) before reporting a vulnerability.

Current limitations and troubleshooting procedures are maintained in
[docs/operations.md](docs/operations.md). Contribution requirements are
defined in [CONTRIBUTING.md](CONTRIBUTING.md).

This project is distributed under the
[MIT License](LICENSE).
