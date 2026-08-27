# Contributing

Contributions should preserve the central rule of the project:

> The LLM proposes. Software validates. The database restricts. Code
> calculates. AI explains.

## Development setup

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Use Python 3.12 or Python 3.13. Keep all credentials in the ignored local
`.env` file and never include credential values in commits or logs.

## Required validation

Before proposing a change, run:

```bash
python -m pip check
python -m ruff check .
python -m ruff format --check .
python -m mypy backend frontend tests/unit/test_containerization.py tests/unit/test_ci_configuration.py
python -m pytest -q
```

Changes to containerization must also validate Compose and both images.
Ephemeral smoke tests must run as the non-root application user and should
use `--network none` when network access is unnecessary.

## Change expectations

- inspect existing behavior before modifying it;
- keep changes narrowly scoped;
- include tests for new behavior and regressions;
- preserve deterministic calculations outside the LLM;
- keep SQL execution read-only and bounded;
- avoid mocks in production code;
- update documentation when commands or contracts change;
- do not weaken validation merely to make a test pass.

## Commits and pull requests

Use focused commits with an imperative subject. A pull request should
explain the problem, the implementation, security implications, and the
exact validation performed. Do not include generated secrets, local
environment files, database dumps, or runtime logs.
