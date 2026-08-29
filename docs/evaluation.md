# Systematic evaluation

## Purpose

The systematic evaluation package provides a deterministic regression gate
for the analytical pipeline. It runs controlled cases, aggregates explicit
quality metrics, compares them with minimum thresholds, and produces a
machine-readable report without sensitive analytical payloads.

## Reference dataset

The packaged dataset is
`backend/app/evaluation/data/reference_questions.yaml`. Its current version is
**1.1.0**, with ten immutable cases:

| Category | Cases | Expected disposition |
| --- | ---: | --- |
| `valid` | 4 | `allow` |
| `ambiguous` | 2 | `clarify` |
| `restricted` | 2 | `block` |
| `out_of_domain` | 2 | `block` |

Identifiers must be unique and all four categories must be represented.
Allowed cases declare controlled semantic criteria and, when applicable, SQL
criteria. Non-allowed cases must stop before SQL generation. The YAML is
included as package data and loaded through `importlib.resources` from source
checkouts and built wheels.

## Quality metrics

Metrics are emitted in this fixed order:

| Metric | Evaluated behavior |
| --- | --- |
| `grounding_accuracy` | Classification and controlled semantic criteria match grounding. |
| `sql_validation_rate` | Generated SQL satisfies validation, grounding, version, reference, and row-limit criteria. |
| `repair_success_rate` | A repair case returns validated SQL within the configured budget. |
| `unsafe_block_rate` | Restricted and unsupported requests are blocked before SQL generation. |
| `calculation_consistency` | Execution, analytics, and visualization evidence remain consistent. |
| `insight_fidelity` | Claims reference existing metric, ranking, series, or visualization evidence with matching versions. |

Every aggregate exposes numerator, denominator, rate, minimum threshold, and
pass/fail status.

## Threshold gate

A metric passes only when its denominator is positive and its rate meets or
exceeds the configured threshold. A zero denominator fails closed. The report
passes only when all six metrics pass. The calibrated dataset provides a
positive denominator for every metric, including one SQL-repair case.

## Offline validation

```bash
.venv/bin/ai-sql-evaluate
```

Equivalent invocation:

```bash
.venv/bin/python -m backend.app.evaluation.cli
```

Offline mode validates syntax, version, identifiers, taxonomy, expectations,
metric applicability, and thresholds. It does not start FastAPI, access the
database, call an LLM, or use the network.

## Explicit runtime evaluation

```bash
.venv/bin/ai-sql-evaluate --run-runtime
```

Runtime mode creates the FastAPI application, enters its lifespan, and obtains
the SQL generation pipeline, query executor, analytics engine, visualization
engine, and insight engine from `app.state`. Non-allowed cases stop before SQL
generation. Runtime mode can require the same local database and provider
configuration as the application and is never selected by default.

## Sanitized report

The JSON report may contain versions, total case count, case identifiers,
categories, dispositions, boolean observations, numerators, denominators,
rates, thresholds, and gate status. It excludes original questions.
It also excludes generated or expected SQL text, query result rows and raw
values, insight claim text, provider payloads, environment variables, and
credentials.

Operational exceptions are converted into sanitized messages and are not
copied verbatim into the report.

## Exit codes

| Exit code | Meaning |
| ---: | --- |
| `0` | Offline validation succeeded, or runtime evaluation passed every threshold. |
| `1` | Runtime evaluation produced a report with at least one failed threshold. |
| `2` | A controlled loading, startup, dependency, or execution failure occurred. |

Exit code `0` indicates success. Exit code `1` indicates a completed regression
gate that failed. Exit code `2` indicates a sanitized operational failure.

## Verification

```bash
.venv/bin/pytest -q tests/unit/evaluation
```

The focused suite contains **49 passing tests**. The complete validated
baseline contains **553 passing tests**:

```bash
.venv/bin/pytest -q
```

Static validation:

```bash
.venv/bin/python -m pip check
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy backend frontend
```

## Limitations

The dataset is deliberately small and deterministic. It protects known
semantic, safety, SQL-repair, calculation, and evidence-fidelity contracts but
does not replace production monitoring or domain-expert review. Runtime
evaluation must be launched deliberately in an environment prepared for its
database and provider dependencies.
