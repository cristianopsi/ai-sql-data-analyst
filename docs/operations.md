# Operations

## Prerequisites

- Python 3.12 or Python 3.13
- Git
- PostgreSQL-compatible client tools
- Docker Engine
- Docker Compose v2

## Environment variables

Create the ignored runtime file from the documented template:

```bash
cp .env.example .env
```

Required Compose variables include:

- `POSTGRES_PASSWORD`
- `APP_DATABASE_USER`
- `APP_DATABASE_PASSWORD`
- `ANALYTICS_DATABASE_USER`
- `ANALYTICS_DATABASE_PASSWORD`

Provider selection uses `LLM_PROVIDER` and provider-specific runtime
configuration. Do not commit `.env`, print its values, or pass credentials
as Docker build arguments.

## Local Python setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Quality validation

```bash
python -m pip check
python -m ruff check .
python -m ruff format --check .
python -m mypy backend frontend tests/unit/test_containerization.py tests/unit/test_ci_configuration.py
python -m pytest -q
```

The current validated baseline is 680 passing tests without forbidden
warnings.

## Visualization integrity controls

The visualization schema requires canonical SHA-256 specification identifiers,
a positive source row count, and unique semantic specifications. KPI
specifications preserve aggregation and total provenance and validate their
average and governed primary value. Table and bar specifications preserve the
full ranking total, validate every share against that total, and must agree when
they describe the same metric and dimension. Line specifications validate
previous values plus controlled absolute and percentage changes.

Unexpected runtime failures from
`POST /api/v1/visualizations/specify` fail closed as sanitized JSON with HTTP
503 and `Cache-Control: no-store`; internal exception details are not returned.
The validated branch-aware backend coverage baseline is 88.21%.

## Grounded insight integrity controls

Grounded insight results require a positive source row count and canonical
SHA-256 claim identifiers derived from normalized claim text and an
order-independent evidence set. Duplicate semantic claims are rejected even when
their evidence references arrive in a different order.

Provider JSON is checked for duplicate object keys before strict model
validation. Numeric validation recognizes conventional and shorthand decimals,
and prohibited SQL material is detected across spaces, tabs, and line breaks.
The provider and model identities in every response must exactly match the
configured managed provider.

The LLM receives only the bounded allowlisted evidence packet. It cannot submit
trusted analytics, alter deterministic calculations, select charts, or introduce
uncited numeric values. Qualitative text without numeric literals is still
provider-authored and is not independently truth-scored; deterministic controls
validate its provenance, evidence, structure, identity, prohibited material, and
bounds.

## Systematic evaluation

Validate the packaged reference dataset without starting application services:

```bash
.venv/bin/ai-sql-evaluate
```

Equivalent module invocation:

```bash
.venv/bin/python -m backend.app.evaluation.cli
```

Offline mode validates dataset version `1.1.0`, ten unique cases, all four
categories, semantic and SQL expectations, six metrics, and minimum
thresholds. It does not start FastAPI, access PostgreSQL, call an LLM, or use
an external network.

Run the real component chain only through the explicit flag:

```bash
.venv/bin/ai-sql-evaluate --run-runtime
```

Runtime mode starts the FastAPI lifespan and injects the configured SQL
generation pipeline, query executor, analytics engine, visualization engine,
and insight engine into the adapter.

The command prints sanitized JSON. Exit code `0` means validation succeeded or
all runtime thresholds passed. Exit code `1` means a report was produced but a
threshold failed. Exit code `2` means a controlled loading, startup,
component-resolution, or execution failure occurred.

Run the 49 focused evaluation tests:

```bash
.venv/bin/pytest -q tests/unit/evaluation
```

The complete validated baseline contains 680 passing tests:

```bash
.venv/bin/pytest -q
```

See `docs/evaluation.md` for the complete operational and privacy contract.

## Docker Compose

Validate interpolation and service configuration before starting:

```bash
docker compose config --quiet
```

Build and start the complete stack:

```bash
docker compose up --build -d
docker compose ps
```

Expected local endpoints:

- backend readiness: `http://127.0.0.1:8000/ready`
- backend OpenAPI: `http://127.0.0.1:8000/openapi.json`
- backend interactive docs: `http://127.0.0.1:8000/docs`
- Streamlit: `http://127.0.0.1:8501`
- Streamlit health: `http://127.0.0.1:8501/_stcore/health`

Check service readiness:

```bash
curl --fail http://127.0.0.1:8000/ready
curl --fail http://127.0.0.1:8501/_stcore/health
```

Stop and remove only the application services while preserving PostgreSQL:

```bash
docker compose stop frontend backend
docker compose rm -f frontend backend
```

A normal `docker compose down` stops the complete stack. Do not add `-v`
unless permanent volume deletion is explicitly intended and backed up.

## Presentation request

The presentation endpoint is:

```text
POST /api/v1/presentations/generate
```

Its public request contains a natural-language question. Client-supplied
SQL is rejected. Generated SQL remains subject to deterministic validation
and read-only database controls.

The executor uses only the analytics pool. Each query starts a read-only
transaction, applies and verifies transaction-local statement, lock, and
idle-in-transaction session timeouts, and verifies the active read-only mode
before executing generated SQL. Pool acquisition uses a separate configured
timeout. Result retrieval uses a bounded `fetchmany` batch of the validated row
limit plus one. Result normalization and contract validation finish inside the
transaction; invariant failures roll back and expose only sanitized responses.

Deterministic analytics verifies semantic and catalog provenance before
calculation. Source numeric precision is retained through aggregation, while
public results use a controlled four-decimal scale. Governed aggregation
determines the downstream primary metric value. Temporal dimensions are parsed
and chronologically ordered under their declared granularities. Summary,
ranking, series, and source-row arithmetic invariants fail closed.

## Troubleshooting

### Compose reports a missing variable

Compare the missing key with `.env.example`. Populate it locally without
printing the value, then run `docker compose config --quiet` again.

### Backend remains unhealthy

Confirm PostgreSQL is healthy, inspect sanitized backend logs, and verify
that internal database URLs use the Compose hostname `postgres`.

### Frontend cannot reach the backend

Inside Compose, `API_BASE_URL` must use the service hostname `backend`,
not `127.0.0.1`. From the host browser, use the loopback Streamlit port.

### A generated query is rejected

Rejection is expected when SQL violates the read-only grammar, references
unknown schema objects, contains unsupported constructs, or exceeds the
bounded repair policy. Do not bypass validation.

### Provider generation fails

Confirm that the selected provider is supported and that its runtime
configuration exists locally. Never log or paste provider credentials.

### Ports are unavailable

Stop the conflicting local process or choose documented loopback port
overrides before starting Compose.

## Operational limitations

- The repository is configured for local and CI validation, not for direct
  internet exposure.
- Production deployment still requires authentication, authorization,
  TLS, network policy, secret management, rate limiting, monitoring,
  backups, and recovery procedures.
- The application supports analytical read-only queries; it is not a
  database administration interface.
- LLM output is advisory and remains subject to deterministic contracts.
- Query limits and timeouts intentionally reject some expensive requests.
- CI builds images but does not publish them to a registry.
- The workflow does not call an external LLM.

## Database provisioning and migrations

A new PostgreSQL volume initially contains only the administrative database and
role created by the official PostgreSQL image. The application role, analytics
role, retail schema, migrations, and least-privilege grants are provisioned
explicitly. Do not start the backend against a new database volume before
completing this sequence.

Keep the real `.env` outside version control and restrict its filesystem
permissions. Never print or commit database URLs, passwords, API keys, or other
credential values.

Use this order for a new database volume:

1. Start PostgreSQL and wait for its health check.
2. Bootstrap the application and analytics database roles.
3. Apply the Alembic migrations with `upgrade head`.
4. Apply the retail schema grants.
5. Optionally load the deterministic demonstration dataset.
6. Start the backend and frontend.
7. Verify the application health and readiness endpoints.

The operational scripts read `/app/.env`. Mount the existing project `.env`
read-only into transient backend containers with a temporary Compose override
stored outside the repository:

```bash
PROVISION_OVERRIDE="/tmp/ai-sql-provision.override.yaml"

cat > "${PROVISION_OVERRIDE}" <<'YAML'
services:
  backend:
    volumes:
      - type: bind
        source: ./.env
        target: /app/.env
        read_only: true
YAML

chmod 600 "${PROVISION_OVERRIDE}"
```

Validate the resolved configuration without printing environment values:

```bash
docker compose \
  -f compose.yaml \
  -f "${PROVISION_OVERRIDE}" \
  config --quiet
```

Start only PostgreSQL and wait until it is healthy:

```bash
docker compose up -d --wait postgres
```

Bootstrap the two least-privilege database roles:

```bash
docker compose \
  -f compose.yaml \
  -f "${PROVISION_OVERRIDE}" \
  run --rm --no-deps backend \
  python scripts/bootstrap_database_roles.py
```

Apply all Alembic migration revisions:

```bash
docker compose \
  -f compose.yaml \
  -f "${PROVISION_OVERRIDE}" \
  run --rm --no-deps backend \
  python -m alembic upgrade head
```

Apply the retail grants after the schema exists:

```bash
docker compose \
  -f compose.yaml \
  -f "${PROVISION_OVERRIDE}" \
  run --rm --no-deps backend \
  python scripts/apply_database_grants.py
```

Loading the deterministic demonstration dataset is optional and is not required
for the health endpoints:

```bash
docker compose \
  -f compose.yaml \
  -f "${PROVISION_OVERRIDE}" \
  run --rm --no-deps backend \
  python scripts/seed_database.py
```

After roles, migrations, and grants succeed, start the application services:

```bash
docker compose up -d --wait backend frontend
```

Verify the backend health and readiness endpoints and the Streamlit health
endpoint through the loopback ports configured in `.env`. Diagnostic output
must not include database URLs, passwords, API keys, response headers, complete
environment values, or container identifiers.

Remove the temporary override only after the transient provisioning containers
have exited:

```bash
rm -f -- "${PROVISION_OVERRIDE}"
```

Run `python -m alembic upgrade head` whenever a deployment introduces new
migration revisions. Role bootstrap and grant application remain controlled
operational steps and must finish before starting a backend that depends on the
new roles, schema, or privileges.
## Configure the DeepSeek API provider

Set `LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-v4-flash`,
`LLM_BASE_URL=https://api.deepseek.com`, and inject `LLM_API_KEY` from the
deployment secret mechanism. Do not commit the key or place it in Compose
files. `deepseek-v4-pro` is the only controlled fallback model. Validate the
health endpoint and mocked provider tests before deployment. Any live API smoke
test requires separate authorization, a strict request limit, and sanitized
logs.
