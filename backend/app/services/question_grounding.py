import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from backend.app.schemas.grounding import (
    GroundedSemanticValue,
    GroundingMatch,
    GroundingStatus,
    QuestionGrounding,
)
from backend.app.schemas.semantic import (
    SemanticLayer,
    SemanticRelationship,
    SemanticScalar,
)
from backend.app.services.semantic_layer import (
    build_semantic_layer,
)

DEFAULT_MAX_QUESTION_LENGTH = 2_000

RESTRICTED_INTENT_TERMS = (
    "email",
    "emails",
    "document number",
    "documento",
    "documentos",
    "cpf",
)

TEMPORAL_EXPRESSION_PATTERN = re.compile(
    r"\b(?:19|20)\d{2}\b"
    r"|\b(?:dia|day|mes|month|trimestre|quarter|ano|year)\b"
)

TARGET_METRICS = {
    "orders_target",
    "revenue_target",
}


class QuestionGroundingError(ValueError):
    """Raised when a question violates the grounding input contract."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    match_type: Literal[
        "metric",
        "dimension",
        "value",
    ]
    semantic_name: str
    matched_term: str
    start: int
    end: int
    dimension_name: str | None = None
    value: SemanticScalar | None = None


def normalize_question(question: str) -> str:
    """Normalize natural-language text for deterministic matching."""
    decomposed = unicodedata.normalize(
        "NFKD",
        question,
    )

    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )

    lowercase = without_accents.lower()

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        lowercase,
    ).strip()


def _normalized_terms(
    *terms: str,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {normalized for term in terms if (normalized := normalize_question(term))},
            key=lambda term: (
                -len(term.split()),
                -len(term),
                term,
            ),
        )
    )


def _find_term(
    normalized_question: str,
    term: str,
) -> tuple[int, int] | None:
    padded_question = f" {normalized_question} "
    padded_term = f" {term} "
    start = padded_question.find(padded_term)

    if start < 0:
        return None

    return start, start + len(term)


def _best_candidate(
    normalized_question: str,
    *,
    match_type: Literal[
        "metric",
        "dimension",
    ],
    semantic_name: str,
    terms: tuple[str, ...],
) -> _Candidate | None:
    matches: list[_Candidate] = []

    for term in _normalized_terms(*terms):
        position = _find_term(
            normalized_question,
            term,
        )

        if position is None:
            continue

        matches.append(
            _Candidate(
                match_type=match_type,
                semantic_name=semantic_name,
                matched_term=term,
                start=position[0],
                end=position[1],
            )
        )

    if not matches:
        return None

    return min(
        matches,
        key=lambda candidate: (
            -len(candidate.matched_term.split()),
            -len(candidate.matched_term),
            candidate.start,
            candidate.matched_term,
        ),
    )


def _remove_contained_candidates(
    candidates: tuple[_Candidate, ...],
) -> tuple[_Candidate, ...]:
    return tuple(
        candidate
        for candidate in candidates
        if not any(
            other.semantic_name != candidate.semantic_name
            and other.start <= candidate.start
            and other.end >= candidate.end
            and (other.end - other.start > candidate.end - candidate.start)
            for other in candidates
        )
    )


def _metric_candidates(
    question: str,
    layer: SemanticLayer,
) -> tuple[_Candidate, ...]:
    candidates = tuple(
        candidate
        for metric in layer.metrics
        if (
            candidate := _best_candidate(
                question,
                match_type="metric",
                semantic_name=metric.name,
                terms=(
                    metric.name,
                    metric.label,
                    *metric.synonyms,
                ),
            )
        )
        is not None
    )

    return _remove_contained_candidates(candidates)


def _dimension_candidates(
    question: str,
    layer: SemanticLayer,
) -> tuple[_Candidate, ...]:
    return tuple(
        candidate
        for dimension in layer.dimensions
        if (
            candidate := _best_candidate(
                question,
                match_type="dimension",
                semantic_name=dimension.name,
                terms=(
                    dimension.name,
                    dimension.label,
                    *dimension.synonyms,
                ),
            )
        )
        is not None
    )


def _value_candidates(
    question: str,
    layer: SemanticLayer,
) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []

    for dimension in layer.dimensions:
        for semantic_value in dimension.values:
            value_text = str(semantic_value.value)

            candidate = _best_candidate(
                question,
                match_type="dimension",
                semantic_name=(f"{dimension.name}:{value_text}"),
                terms=(
                    value_text,
                    semantic_value.label,
                    *semantic_value.synonyms,
                ),
            )

            if candidate is None:
                continue

            candidates.append(
                _Candidate(
                    match_type="value",
                    semantic_name=(candidate.semantic_name),
                    matched_term=(candidate.matched_term),
                    start=candidate.start,
                    end=candidate.end,
                    dimension_name=dimension.name,
                    value=semantic_value.value,
                )
            )

    return tuple(candidates)


def _resolve_value_candidates(
    candidates: tuple[_Candidate, ...],
    explicit_dimensions: set[str],
) -> tuple[tuple[_Candidate, ...], bool]:
    grouped: dict[
        str,
        list[_Candidate],
    ] = defaultdict(list)

    for candidate in candidates:
        grouped[candidate.matched_term].append(candidate)

    resolved: list[_Candidate] = []
    ambiguous = False

    for matched_term in sorted(grouped):
        current_candidates = grouped[matched_term]

        explicitly_selected = [
            candidate
            for candidate in current_candidates
            if candidate.dimension_name in explicit_dimensions
        ]

        if explicitly_selected:
            resolved.extend(explicitly_selected)
            continue

        dimensions = {candidate.dimension_name for candidate in current_candidates}

        if len(dimensions) > 1:
            ambiguous = True

        resolved.extend(current_candidates)

    return (
        tuple(
            sorted(
                resolved,
                key=lambda candidate: (
                    candidate.semantic_name,
                    candidate.matched_term,
                ),
            )
        ),
        ambiguous,
    )


def _temporal_dimensions(
    question: str,
    selected_metrics: set[str],
) -> tuple[str, ...]:
    if not selected_metrics or TEMPORAL_EXPRESSION_PATTERN.search(question) is None:
        return ()

    target_metrics = selected_metrics & TARGET_METRICS
    actual_metrics = selected_metrics - TARGET_METRICS

    dimensions: list[str] = []

    if actual_metrics:
        dimensions.append("order_date")

    if target_metrics:
        dimensions.append("target_month")

    return tuple(dimensions)


def _shortest_relationship_path(
    start_table: str,
    target_table: str,
    relationships: tuple[
        SemanticRelationship,
        ...,
    ],
) -> tuple[str, ...]:
    if start_table == target_table:
        return ()

    adjacency: dict[
        str,
        list[tuple[str, str]],
    ] = defaultdict(list)

    for relationship in relationships:
        from_table = relationship.from_column.table_name
        to_table = relationship.to_column.table_name

        adjacency[from_table].append(
            (
                to_table,
                relationship.name,
            )
        )
        adjacency[to_table].append(
            (
                from_table,
                relationship.name,
            )
        )

    queue: deque[tuple[str, tuple[str, ...]]] = deque([(start_table, ())])
    visited = {start_table}

    while queue:
        current_table, path = queue.popleft()

        for neighbor, relationship_name in sorted(adjacency[current_table]):
            if neighbor in visited:
                continue

            next_path = (
                *path,
                relationship_name,
            )

            if neighbor == target_table:
                return next_path

            visited.add(neighbor)
            queue.append(
                (
                    neighbor,
                    next_path,
                )
            )

    raise QuestionGroundingError("Selected semantic tables are not connected")


def _relevant_relationships(
    requested_tables: tuple[str, ...],
    layer: SemanticLayer,
) -> tuple[str, ...]:
    if len(requested_tables) < 2:
        return ()

    anchor = requested_tables[0]
    selected: set[str] = set()

    for target_table in requested_tables[1:]:
        selected.update(
            _shortest_relationship_path(
                anchor,
                target_table,
                layer.relationships,
            )
        )

    return tuple(sorted(selected))


def _restricted_result(
    normalized_question: str,
    layer: SemanticLayer,
) -> QuestionGrounding:
    return QuestionGrounding(
        semantic_version=layer.semantic_version,
        status="restricted",
        normalized_question=normalized_question,
    )


def ground_question(
    question: str,
    semantic_layer: SemanticLayer | None = None,
    *,
    max_question_length: int = (DEFAULT_MAX_QUESTION_LENGTH),
) -> QuestionGrounding:
    """Ground a question against safe deterministic semantics."""
    if max_question_length < 1:
        raise QuestionGroundingError("max_question_length must be positive")

    stripped_question = question.strip()

    if not stripped_question:
        raise QuestionGroundingError("Question cannot be empty")

    if len(stripped_question) > max_question_length:
        raise QuestionGroundingError("Question exceeds maximum length")

    normalized_question = normalize_question(stripped_question)

    if not normalized_question:
        raise QuestionGroundingError(
            "Question must contain letters or numbers"
        )

    layer = semantic_layer if semantic_layer is not None else build_semantic_layer()

    if any(
        _find_term(
            normalized_question,
            restricted_term,
        )
        is not None
        for restricted_term in (RESTRICTED_INTENT_TERMS)
    ):
        return _restricted_result(
            normalized_question,
            layer,
        )

    metric_candidates = _metric_candidates(
        normalized_question,
        layer,
    )
    dimension_candidates = _dimension_candidates(
        normalized_question,
        layer,
    )

    explicit_dimensions = {candidate.semantic_name for candidate in dimension_candidates}

    value_candidates, values_ambiguous = _resolve_value_candidates(
        _value_candidates(
            normalized_question,
            layer,
        ),
        explicit_dimensions,
    )

    selected_metric_names = {candidate.semantic_name for candidate in metric_candidates}

    selected_dimension_names = {
        *explicit_dimensions,
        *(
            candidate.dimension_name
            for candidate in value_candidates
            if candidate.dimension_name is not None
        ),
        *_temporal_dimensions(
            normalized_question,
            selected_metric_names,
        ),
    }

    requested_tables: list[str] = []

    def add_table(table_name: str) -> None:
        if table_name not in requested_tables:
            requested_tables.append(table_name)

    for metric in layer.metrics:
        if metric.name not in selected_metric_names:
            continue

        add_table(metric.source.table_name)

        for metric_filter in metric.filters:
            add_table(metric_filter.source.table_name)

    for dimension in layer.dimensions:
        if dimension.name in selected_dimension_names:
            add_table(dimension.source.table_name)

    relationship_names = _relevant_relationships(
        tuple(requested_tables),
        layer,
    )

    relationships_by_name = {
        relationship.name: relationship for relationship in layer.relationships
    }
    relevant_tables = set(requested_tables)

    for relationship_name in relationship_names:
        relationship = relationships_by_name[relationship_name]
        relevant_tables.add(relationship.from_column.table_name)
        relevant_tables.add(relationship.to_column.table_name)

    selected_rules = tuple(
        rule.name
        for rule in layer.business_rules
        if selected_metric_names & set(rule.related_metrics)
    )

    matches = tuple(
        GroundingMatch(
            match_type=candidate.match_type,
            semantic_name=candidate.semantic_name,
            matched_term=candidate.matched_term,
        )
        for candidate in sorted(
            (
                *metric_candidates,
                *dimension_candidates,
                *value_candidates,
            ),
            key=lambda candidate: (
                candidate.match_type,
                candidate.semantic_name,
                candidate.matched_term,
            ),
        )
    )

    grounded_values = tuple(
        GroundedSemanticValue(
            dimension_name=(candidate.dimension_name or ""),
            value=(candidate.value if candidate.value is not None else ""),
            matched_term=(candidate.matched_term),
        )
        for candidate in value_candidates
    )

    status: GroundingStatus

    if not (selected_metric_names or selected_dimension_names or grounded_values):
        status = "unsupported"
    elif values_ambiguous or not selected_metric_names:
        status = "ambiguous"
    else:
        status = "grounded"

    return QuestionGrounding(
        semantic_version=layer.semantic_version,
        status=status,
        normalized_question=normalized_question,
        metrics=tuple(
            metric.name for metric in layer.metrics if metric.name in selected_metric_names
        ),
        dimensions=tuple(
            dimension.name
            for dimension in layer.dimensions
            if dimension.name in selected_dimension_names
        ),
        values=grounded_values,
        tables=tuple(sorted(relevant_tables)),
        relationships=relationship_names,
        business_rules=selected_rules,
        matches=matches,
    )
