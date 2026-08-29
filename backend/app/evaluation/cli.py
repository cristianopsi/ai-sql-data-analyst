from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO

from fastapi import FastAPI

from backend.app.evaluation.contracts import (
    ALL_EVALUATION_METRICS,
    EvaluationDataset,
)
from backend.app.evaluation.dataset import (
    load_reference_evaluation_dataset,
)
from backend.app.evaluation.pipeline_adapter import (
    GroundingCallable,
    RealPipelineEvaluationAdapter,
)
from backend.app.evaluation.runner import EvaluationRunner
from backend.app.services.question_grounding import ground_question

ApplicationFactory = Callable[[], FastAPI]


def _create_application() -> FastAPI:
    from backend.app.main import create_app

    return create_app()


def _dataset_summary(dataset: EvaluationDataset) -> dict[str, object]:
    categories = Counter(case.category.value for case in dataset.cases)

    return {
        "mode": "validate-dataset",
        "status": "valid",
        "dataset_version": dataset.dataset_version,
        "total_cases": len(dataset.cases),
        "categories": dict(sorted(categories.items())),
        "metrics": [metric.value for metric in ALL_EVALUATION_METRICS],
        "thresholds": dataset.thresholds.model_dump(mode="json"),
    }


def _write_json(
    payload: Mapping[str, object],
    *,
    stream: TextIO | None = None,
) -> None:
    target = stream if stream is not None else sys.stdout
    target.write(
        json.dumps(
            payload,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    )
    target.write("\n")


def _create_runtime_runner(
    application: FastAPI,
    *,
    grounder: GroundingCallable = ground_question,
) -> EvaluationRunner:
    adapter = RealPipelineEvaluationAdapter(
        pipeline=application.state.sql_generation_pipeline,
        executor=application.state.query_executor,
        analytics_engine=application.state.analytics_engine,
        visualization_engine=application.state.visualization_engine,
        insight_engine=application.state.insight_engine,
        grounder=grounder,
    )
    return EvaluationRunner(adapter)


async def _run_runtime(
    app_factory: ApplicationFactory | None = None,
    *,
    grounder: GroundingCallable = ground_question,
) -> int:
    factory = app_factory if app_factory is not None else _create_application
    application = factory()
    dataset = load_reference_evaluation_dataset()

    async with application.router.lifespan_context(application):
        runner = _create_runtime_runner(
            application,
            grounder=grounder,
        )
        report = runner.run(dataset)

    sys.stdout.write(report.model_dump_json(indent=2))
    sys.stdout.write("\n")
    return 0 if report.passed else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-sql-evaluate",
        description=(
            "Validate the packaged reference evaluation dataset or "
            "explicitly execute it through the configured runtime."
        ),
    )
    parser.add_argument(
        "--run-runtime",
        action="store_true",
        help=(
            "start the FastAPI lifespan and execute the reference "
            "dataset through the configured database and LLM runtime"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)

    try:
        if arguments.run_runtime:
            return asyncio.run(_run_runtime())

        dataset = load_reference_evaluation_dataset()
        _write_json(_dataset_summary(dataset))
        return 0
    except Exception:  # noqa: BLE001
        _write_json(
            {
                "status": "error",
                "error": "evaluation_failed",
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
