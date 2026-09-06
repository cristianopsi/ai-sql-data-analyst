"""Billing webhook API router.

Provides endpoint for processing billing webhooks (e.g., plan changes)
with idempotency guarantee.

In production, this endpoint would be protected by webhook signature
verification (e.g., Stripe signature header). For MVP, it is publicly
accessible without auth.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.core.plans import BillingWebhookProcessor
from backend.app.schemas.billing import (
    BillingWebhookRequest,
    BillingWebhookResponse,
)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

_webhook_processor = BillingWebhookProcessor()


@router.post("/webhook", response_model=BillingWebhookResponse)
async def process_billing_webhook(
    request: BillingWebhookRequest,
) -> BillingWebhookResponse:
    """Process a billing webhook event idempotently.

    Accepts billing events (e.g., subscription created, plan changed)
    and processes them with idempotency via event_id deduplication.

    Returns:
        BillingWebhookResponse with processing status.
    """
    result = _webhook_processor.process(
        event_id=request.event_id,
        event_type=request.event_type,
        tenant_id=request.tenant_id,
        new_plan=request.new_plan,
    )
    return BillingWebhookResponse(
        status=result["status"],
        event_id=result["event_id"],
        message=result["message"],
    )
