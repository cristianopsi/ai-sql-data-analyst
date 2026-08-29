from __future__ import annotations

from collections.abc import Mapping, Sequence
from importlib.resources import files
from typing import Final

import yaml
from pydantic import ValidationError

from backend.app.evaluation.contracts import EvaluationDataset

REFERENCE_DATASET_PACKAGE: Final = "backend.app.evaluation.data"
REFERENCE_DATASET_NAME: Final = "reference_questions.yaml"
MAX_REFERENCE_DATASET_BYTES: Final = 131_072

_SENSITIVE_KEY_FRAGMENTS: Final = (
    "password",
    "credential",
    "private_key",
    "api_key",
    "access_key",
    "secret",
    "token",
)


class EvaluationDatasetError(ValueError):
    """Raised when the reference dataset violates its safe contract."""


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested_value in value.items():
            if not isinstance(raw_key, str):
                raise EvaluationDatasetError("evaluation dataset mapping keys must be strings")

            normalized_key = raw_key.casefold().replace("-", "_")

            if any(fragment in normalized_key for fragment in _SENSITIVE_KEY_FRAGMENTS):
                raise EvaluationDatasetError("evaluation dataset contains a sensitive key")

            _reject_sensitive_keys(nested_value)

        return

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for item in value:
            _reject_sensitive_keys(item)


def parse_evaluation_dataset(content: str) -> EvaluationDataset:
    encoded_size = len(content.encode("utf-8"))

    if encoded_size == 0 or encoded_size > MAX_REFERENCE_DATASET_BYTES:
        raise EvaluationDatasetError("evaluation dataset size is outside the allowed boundary")

    try:
        raw_dataset: object = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise EvaluationDatasetError("reference evaluation dataset is invalid") from exc

    if not isinstance(raw_dataset, Mapping):
        raise EvaluationDatasetError("reference evaluation dataset must be a mapping")

    _reject_sensitive_keys(raw_dataset)

    try:
        return EvaluationDataset.model_validate(raw_dataset)
    except ValidationError as exc:
        raise EvaluationDatasetError("reference evaluation dataset is invalid") from exc


def load_reference_evaluation_dataset() -> EvaluationDataset:
    resource = files(REFERENCE_DATASET_PACKAGE).joinpath(REFERENCE_DATASET_NAME)

    try:
        content = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise EvaluationDatasetError("reference evaluation dataset is unavailable") from exc

    return parse_evaluation_dataset(content)
