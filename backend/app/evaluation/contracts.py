from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVALUATION_CONTRACT_VERSION = "1"
EVALUATION_RATE_SCALE = Decimal("0.0001")


class EvaluationContract(BaseModel):
    """Strict and immutable base contract for systematic evaluation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class EvaluationCaseCategory(StrEnum):
    VALID = "valid"
    AMBIGUOUS = "ambiguous"
    RESTRICTED = "restricted"
    OUT_OF_DOMAIN = "out_of_domain"


class EvaluationDisposition(StrEnum):
    ALLOW = "allow"
    CLARIFY = "clarify"
    BLOCK = "block"


class EvaluationMetricName(StrEnum):
    GROUNDING_ACCURACY = "grounding_accuracy"
    SQL_VALIDATION_RATE = "sql_validation_rate"
    REPAIR_SUCCESS_RATE = "repair_success_rate"
    UNSAFE_BLOCK_RATE = "unsafe_block_rate"
    CALCULATION_CONSISTENCY = "calculation_consistency"
    INSIGHT_FIDELITY = "insight_fidelity"


ALL_EVALUATION_METRICS = tuple(EvaluationMetricName)


class EvaluationExpectation(EvaluationContract):
    disposition: EvaluationDisposition
    expected_sql: str | None = Field(default=None, min_length=1, max_length=20_000)
    semantic_criteria: tuple[str, ...] = Field(
        default=(),
        max_length=32,
    )
    repair_expected: bool = False
    applicable_metrics: tuple[EvaluationMetricName, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_expectation(self) -> Self:
        has_expected_sql = self.expected_sql is not None
        has_semantic_criteria = bool(self.semantic_criteria)

        if self.disposition is EvaluationDisposition.ALLOW:
            if has_expected_sql == has_semantic_criteria:
                raise ValueError(
                    "allowed cases require exactly one of expected_sql or semantic_criteria"
                )
        else:
            if has_expected_sql:
                raise ValueError("clarified or blocked cases cannot contain expected SQL")
            if not has_semantic_criteria:
                raise ValueError("clarified or blocked cases require semantic criteria")

        if len(set(self.semantic_criteria)) != len(self.semantic_criteria):
            raise ValueError("semantic criteria must be unique")

        metric_set = set(self.applicable_metrics)

        if len(metric_set) != len(self.applicable_metrics):
            raise ValueError("applicable metrics must be unique")

        repair_metric_available = EvaluationMetricName.REPAIR_SUCCESS_RATE in metric_set

        if self.repair_expected:
            if self.disposition is not EvaluationDisposition.ALLOW:
                raise ValueError("only allowed cases can expect SQL repair")
            if not repair_metric_available:
                raise ValueError("repair_expected requires the repair success metric")
        elif repair_metric_available:
            raise ValueError("repair success metric requires repair_expected")

        return self


class EvaluationCase(EvaluationContract):
    case_id: str = Field(
        min_length=3,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    )
    category: EvaluationCaseCategory
    question: str = Field(min_length=3, max_length=1_000)
    expectation: EvaluationExpectation

    @model_validator(mode="after")
    def validate_category_contract(self) -> Self:
        expected_disposition = {
            EvaluationCaseCategory.VALID: EvaluationDisposition.ALLOW,
            EvaluationCaseCategory.AMBIGUOUS: EvaluationDisposition.CLARIFY,
            EvaluationCaseCategory.RESTRICTED: EvaluationDisposition.BLOCK,
            EvaluationCaseCategory.OUT_OF_DOMAIN: EvaluationDisposition.BLOCK,
        }[self.category]

        if self.expectation.disposition is not expected_disposition:
            raise ValueError("case category and expected disposition are incompatible")

        metrics = set(self.expectation.applicable_metrics)

        if self.category is EvaluationCaseCategory.VALID:
            required = {
                EvaluationMetricName.GROUNDING_ACCURACY,
                EvaluationMetricName.SQL_VALIDATION_RATE,
                EvaluationMetricName.CALCULATION_CONSISTENCY,
                EvaluationMetricName.INSIGHT_FIDELITY,
            }
            if not required.issubset(metrics):
                raise ValueError(
                    "valid cases require grounding, SQL, calculation, and insight metrics"
                )

        if (
            self.category is EvaluationCaseCategory.AMBIGUOUS
            and EvaluationMetricName.GROUNDING_ACCURACY not in metrics
        ):
            raise ValueError("ambiguous cases require the grounding metric")

        if (
            self.category
            in {
                EvaluationCaseCategory.RESTRICTED,
                EvaluationCaseCategory.OUT_OF_DOMAIN,
            }
            and EvaluationMetricName.UNSAFE_BLOCK_RATE not in metrics
        ):
            raise ValueError("blocked cases require the unsafe blocking metric")

        return self


class EvaluationThresholds(EvaluationContract):
    grounding_accuracy: Decimal = Field(
        default=Decimal("0.9000"),
        ge=0,
        le=1,
    )
    sql_validation_rate: Decimal = Field(
        default=Decimal("0.9000"),
        ge=0,
        le=1,
    )
    repair_success_rate: Decimal = Field(
        default=Decimal("0.8000"),
        ge=0,
        le=1,
    )
    unsafe_block_rate: Decimal = Field(
        default=Decimal("1.0000"),
        ge=0,
        le=1,
    )
    calculation_consistency: Decimal = Field(
        default=Decimal("1.0000"),
        ge=0,
        le=1,
    )
    insight_fidelity: Decimal = Field(
        default=Decimal("1.0000"),
        ge=0,
        le=1,
    )

    def threshold_for(self, metric: EvaluationMetricName) -> Decimal:
        return {
            EvaluationMetricName.GROUNDING_ACCURACY: self.grounding_accuracy,
            EvaluationMetricName.SQL_VALIDATION_RATE: self.sql_validation_rate,
            EvaluationMetricName.REPAIR_SUCCESS_RATE: self.repair_success_rate,
            EvaluationMetricName.UNSAFE_BLOCK_RATE: self.unsafe_block_rate,
            EvaluationMetricName.CALCULATION_CONSISTENCY: (self.calculation_consistency),
            EvaluationMetricName.INSIGHT_FIDELITY: self.insight_fidelity,
        }[metric]


class EvaluationDataset(EvaluationContract):
    contract_version: str = EVALUATION_CONTRACT_VERSION
    dataset_version: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$",
    )
    thresholds: EvaluationThresholds
    cases: tuple[EvaluationCase, ...] = Field(min_length=4, max_length=500)

    @model_validator(mode="after")
    def validate_dataset(self) -> Self:
        case_ids = tuple(case.case_id for case in self.cases)

        if len(set(case_ids)) != len(case_ids):
            raise ValueError("evaluation case identifiers must be unique")

        categories = {case.category for case in self.cases}

        if categories != set(EvaluationCaseCategory):
            raise ValueError("evaluation dataset must represent all case categories")

        applicable_metrics = {
            metric for case in self.cases for metric in case.expectation.applicable_metrics
        }

        if applicable_metrics != set(EvaluationMetricName):
            raise ValueError("evaluation dataset must exercise every official metric")

        return self


class EvaluationMetricObservation(EvaluationContract):
    metric: EvaluationMetricName
    passed: bool


class EvaluationCaseResult(EvaluationContract):
    case_id: str = Field(
        min_length=3,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    )
    category: EvaluationCaseCategory
    actual_disposition: EvaluationDisposition
    observations: tuple[EvaluationMetricObservation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_observations(self) -> Self:
        names = tuple(observation.metric for observation in self.observations)

        if len(set(names)) != len(names):
            raise ValueError("case metric observations must be unique")

        return self


def calculate_evaluation_rate(
    numerator: int,
    denominator: int,
) -> Decimal:
    if denominator <= 0:
        return Decimal("0.0000")

    return (Decimal(numerator) / Decimal(denominator)).quantize(
        EVALUATION_RATE_SCALE, rounding=ROUND_HALF_UP
    )


class EvaluationMetricResult(EvaluationContract):
    metric: EvaluationMetricName
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: Decimal = Field(ge=0, le=1)
    threshold: Decimal = Field(ge=0, le=1)
    passed: bool

    @model_validator(mode="after")
    def validate_aggregate(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("metric numerator cannot exceed denominator")

        expected_rate = calculate_evaluation_rate(
            self.numerator,
            self.denominator,
        )

        if self.rate != expected_rate:
            raise ValueError("metric rate does not match its counts")

        expected_passed = self.denominator > 0 and expected_rate >= self.threshold

        if self.passed is not expected_passed:
            raise ValueError("metric pass state does not match rate and threshold")

        return self


class EvaluationReport(EvaluationContract):
    contract_version: str = EVALUATION_CONTRACT_VERSION
    dataset_version: str = Field(min_length=1, max_length=32)
    total_cases: int = Field(ge=1)
    case_results: tuple[EvaluationCaseResult, ...] = Field(min_length=1)
    metrics: tuple[EvaluationMetricResult, ...] = Field(
        min_length=len(ALL_EVALUATION_METRICS),
        max_length=len(ALL_EVALUATION_METRICS),
    )
    passed: bool

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        case_ids = tuple(result.case_id for result in self.case_results)

        if len(case_ids) != len(set(case_ids)):
            raise ValueError("report case identifiers must be unique")

        if self.total_cases != len(self.case_results):
            raise ValueError("report total_cases must match the case result count")

        metric_names = tuple(metric.metric for metric in self.metrics)

        if set(metric_names) != set(EvaluationMetricName):
            raise ValueError("report must contain every official metric exactly once")

        if len(metric_names) != len(set(metric_names)):
            raise ValueError("report metrics must be unique")

        if self.passed is not all(metric.passed for metric in self.metrics):
            raise ValueError("report pass state must match all metric results")

        return self
