from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class DatabaseReadiness(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "unavailable"]


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ready", "not_ready"]
    application_database: DatabaseReadiness
    analytics_database: DatabaseReadiness
