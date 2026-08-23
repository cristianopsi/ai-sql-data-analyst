from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

type LLMMessageRole = Literal[
    "system",
    "user",
    "assistant",
]
type LLMResponseFormat = Literal[
    "text",
    "json",
]
type LLMFinishReason = Literal[
    "stop",
    "length",
    "content_filter",
]


class LLMMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: LLMMessageRole
    content: str = Field(min_length=1)


class LLMGenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: tuple[LLMMessage, ...] = Field(
        min_length=1,
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
    )
    max_tokens: int = Field(
        default=1200,
        ge=1,
        le=32768,
    )
    response_format: LLMResponseFormat = "json"

    @model_validator(mode="after")
    def require_user_message(self) -> Self:
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("LLM request must contain at least one user message")

        return self


class LLMTokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMGenerationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    content: str = Field(min_length=1)
    finish_reason: LLMFinishReason
    usage: LLMTokenUsage
