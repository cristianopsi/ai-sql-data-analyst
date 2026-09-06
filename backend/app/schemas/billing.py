"""Pydantic schemas for billing webhook endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BillingWebhookRequest(BaseModel):
    """Request body for billing webhook."""

    event_id: str = Field(..., min_length=1, description="Unique event identifier")
    event_type: str = Field(..., min_length=1, description="Event type")
    tenant_id: str = Field(..., min_length=1, description="Tenant identifier")
    new_plan: str = Field(..., min_length=1, description="New plan name")


class BillingWebhookResponse(BaseModel):
    """Response body for billing webhook."""

    status: str = Field(..., description="Processing status")
    event_id: str = Field(..., description="Event identifier")
    message: str = Field(..., description="Status message")
