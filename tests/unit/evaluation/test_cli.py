from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import backend.app.evaluation.cli as cli
from backend.app.evaluation import (
    ALL_EVALUATION_METRICS,
    load_reference_evaluation_dataset,
)


def test_dataset_summary_is_sanitized_and_complete() -> None:
    dataset = load_reference_evaluation_dataset()

    summary = cli._dataset_summary(dataset)
    serialized = json.dumps(summary, sort_keys=True).lower()

    assert summary["mode"] == "validate-dataset"
    assert summary["status"] == "valid"
    assert summary["dataset_version"] == "1.1.0"
    assert summary["total_cases"] == 10
    assert summary["metrics"] == [metric.value for metric in ALL_EVALUATION_METRICS]
    assert summary["categories"] == {
        "ambiguous": 2,
        "out_of_domain": 2,
        "restricted": 2,
        "valid": 4,
    }
    assert "thresholds" in summary
    assert "question" not in serialized
    assert '"sql"' not in serialized
    assert '"rows"' not in serialized
    assert "credential" not in serialized
    assert "api_key" not in serialized


def test_default_command_validates_dataset_without_runtime(
    capsys: Any,
) -> None:
    exit_code = cli.main([])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""
    assert payload["mode"] == "validate-dataset"
    assert payload["status"] == "valid"
    assert payload["total_cases"] == 10


def test_runtime_flag_requires_explicit_selection(
    monkeypatch: Any,
) -> None:
    called = False

    async def fake_run_runtime() -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(
        cli,
        "_run_runtime",
        fake_run_runtime,
    )

    assert cli.main(["--run-runtime"]) == 0
    assert called is True


def test_runtime_runner_injects_all_lifespan_components(
    monkeypatch: Any,
) -> None:
    captured: dict[str, object] = {}

    class CapturingAdapter:
        def __init__(self, **components: object) -> None:
            captured.update(components)

    class CapturingRunner:
        def __init__(self, executor: object) -> None:
            self.executor = executor

    state = SimpleNamespace(
        sql_generation_pipeline=object(),
        query_executor=object(),
        analytics_engine=object(),
        visualization_engine=object(),
        insight_engine=object(),
    )
    application = SimpleNamespace(state=state)

    monkeypatch.setattr(
        cli,
        "RealPipelineEvaluationAdapter",
        CapturingAdapter,
    )
    monkeypatch.setattr(
        cli,
        "EvaluationRunner",
        CapturingRunner,
    )

    runner = cli._create_runtime_runner(application)

    assert isinstance(runner, CapturingRunner)
    assert set(captured) == {
        "pipeline",
        "executor",
        "analytics_engine",
        "visualization_engine",
        "insight_engine",
        "grounder",
    }
    assert captured["pipeline"] is state.sql_generation_pipeline
    assert captured["executor"] is state.query_executor
    assert captured["analytics_engine"] is state.analytics_engine
    assert captured["visualization_engine"] is state.visualization_engine
    assert captured["insight_engine"] is state.insight_engine


def _runtime_application(
    *,
    passed: bool,
    lifecycle: list[str],
) -> tuple[object, object]:
    @asynccontextmanager
    async def lifespan(application: object) -> Any:
        lifecycle.append("started")
        yield
        lifecycle.append("stopped")

    application = SimpleNamespace(
        router=SimpleNamespace(
            lifespan_context=lifespan,
        )
    )

    class Report:
        def __init__(self) -> None:
            self.passed = passed

        def model_dump_json(self, *, indent: int) -> str:
            assert indent == 2
            return json.dumps({"passed": self.passed})

    class Runner:
        def run(self, dataset: object) -> Report:
            assert dataset is not None
            return Report()

    return application, Runner()


def test_runtime_command_returns_zero_for_passing_report(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    lifecycle: list[str] = []
    application, runner = _runtime_application(
        passed=True,
        lifecycle=lifecycle,
    )

    monkeypatch.setattr(
        cli,
        "_create_runtime_runner",
        lambda application, grounder: runner,
    )

    exit_code = asyncio.run(cli._run_runtime(lambda: application))

    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out) == {"passed": True}
    assert lifecycle == ["started", "stopped"]


def test_runtime_command_returns_one_for_failed_thresholds(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    lifecycle: list[str] = []
    application, runner = _runtime_application(
        passed=False,
        lifecycle=lifecycle,
    )

    monkeypatch.setattr(
        cli,
        "_create_runtime_runner",
        lambda application, grounder: runner,
    )

    exit_code = asyncio.run(cli._run_runtime(lambda: application))

    captured = capsys.readouterr()

    assert exit_code == 1
    assert json.loads(captured.out) == {"passed": False}
    assert lifecycle == ["started", "stopped"]


def test_runtime_failure_is_sanitized(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    async def failing_runtime() -> int:
        raise RuntimeError("sensitive-provider-value")

    monkeypatch.setattr(
        cli,
        "_run_runtime",
        failing_runtime,
    )

    exit_code = cli.main(["--run-runtime"])
    captured = capsys.readouterr()
    payload = json.loads(captured.err)

    assert exit_code == 2
    assert captured.out == ""
    assert payload == {
        "status": "error",
        "error": "evaluation_failed",
    }
    assert "sensitive-provider-value" not in captured.err
