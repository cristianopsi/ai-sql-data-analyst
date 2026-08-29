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

The current validated baseline is 553 passing tests without forbidden
warnings.

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

The complete validated baseline contains 553 passing tests:

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
