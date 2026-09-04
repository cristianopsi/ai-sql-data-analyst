"""Strict contracts for in-memory PowerPoint presentation artifacts."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PPTX_MIME_TYPE: Literal[
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
] = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MAX_PRESENTATION_ARTIFACT_BYTES = 5_242_880
MAX_PRESENTATION_SLIDES = 20


class PresentationArtifact(BaseModel):
    """Validated in-memory PPTX generated from one trusted presentation result."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    artifact_version: Literal["1"] = "1"
    artifact_status: Literal["generated"] = "generated"
    artifact_format: Literal["pptx"] = "pptx"
    artifact_id: str = Field(
        min_length=24,
        max_length=24,
        pattern=r"^[0-9a-f]{24}$",
    )
    filename: str = Field(
        min_length=53,
        max_length=53,
        pattern=r"^analytical-presentation-[0-9a-f]{24}\.pptx$",
    )
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ] = PPTX_MIME_TYPE
    content: bytes = Field(
        min_length=1,
        max_length=MAX_PRESENTATION_ARTIFACT_BYTES,
    )
    size_bytes: int = Field(
        ge=1,
        le=MAX_PRESENTATION_ARTIFACT_BYTES,
    )
    slide_count: int = Field(
        ge=1,
        le=MAX_PRESENTATION_SLIDES,
    )
    source_row_count: int = Field(ge=1)
    presentation_version: str = Field(min_length=1)
    visualization_version: str = Field(min_length=1)
    insight_version: str = Field(min_length=1)
    analytics_version: str = Field(min_length=1)
    execution_version: str = Field(min_length=1)
    semantic_version: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_artifact_contract(self) -> Self:
        """Require canonical naming, exact size, and a ZIP-based PPTX payload."""

        expected_filename = f"analytical-presentation-{self.artifact_id}.pptx"

        if self.filename != expected_filename:
            raise ValueError("Presentation artifact filename is not canonical")

        if self.size_bytes != len(self.content):
            raise ValueError("Presentation artifact size does not match its content")

        if not self.content.startswith(b"PK\x03\x04"):
            raise ValueError("Presentation artifact is not an OOXML ZIP package")

        return self
