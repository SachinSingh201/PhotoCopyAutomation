import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Set
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.exceptions import AppException, ErrorCode
from backend.core.logging import logger
from backend.models.audit import AuditLog
from backend.models.order import Order


class OrderState(str, Enum):
    CREATED = "CREATED"
    FILES_UPLOADING = "FILES_UPLOADING"
    FILES_UPLOADED = "FILES_UPLOADED"
    CONFIGURATION_PENDING = "CONFIGURATION_PENDING"
    CONFIGURED = "CONFIGURED"
    PRICE_CALCULATED = "PRICE_CALCULATED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    WAITING_FOR_PRINT_WINDOW = "WAITING_FOR_PRINT_WINDOW"
    QUEUED = "QUEUED"
    PRINTING = "PRINTING"
    PRINT_FAILED = "PRINT_FAILED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    PRINT_RESULT_UNKNOWN = "PRINT_RESULT_UNKNOWN"
    PRINTED = "PRINTED"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    COLLECTED = "COLLECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    CLEANED = "CLEANED"


# Legal transitions matrix as specified in improvisation.md §4 & §20
LEGAL_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.CREATED: {OrderState.FILES_UPLOADING},
    OrderState.FILES_UPLOADING: {OrderState.FILES_UPLOADED, OrderState.EXPIRED},
    OrderState.FILES_UPLOADED: {OrderState.CONFIGURATION_PENDING, OrderState.EXPIRED},
    OrderState.CONFIGURATION_PENDING: {OrderState.CONFIGURED, OrderState.EXPIRED},
    OrderState.CONFIGURED: {OrderState.PRICE_CALCULATED},
    OrderState.PRICE_CALCULATED: {OrderState.AWAITING_CONFIRMATION},
    OrderState.AWAITING_CONFIRMATION: {OrderState.PAYMENT_PENDING, OrderState.CONFIGURATION_PENDING},
    OrderState.PAYMENT_PENDING: {OrderState.PAID, OrderState.EXPIRED},
    OrderState.PAID: {OrderState.QUEUED, OrderState.WAITING_FOR_PRINT_WINDOW},
    OrderState.WAITING_FOR_PRINT_WINDOW: {OrderState.QUEUED, OrderState.CANCELLED},
    OrderState.QUEUED: {OrderState.PRINTING, OrderState.CANCELLED, OrderState.MANUAL_REQUIRED},
    OrderState.PRINTING: {OrderState.PRINTED, OrderState.PRINT_FAILED, OrderState.MANUAL_REQUIRED, OrderState.PRINT_RESULT_UNKNOWN},
    OrderState.PRINT_FAILED: {OrderState.PRINTING, OrderState.MANUAL_REQUIRED, OrderState.QUEUED},
    OrderState.PRINT_RESULT_UNKNOWN: {OrderState.PRINTED, OrderState.MANUAL_REQUIRED, OrderState.PRINT_FAILED},
    OrderState.MANUAL_REQUIRED: {OrderState.PRINTING, OrderState.PRINTED},
    OrderState.PRINTED: {OrderState.READY_FOR_PICKUP},
    OrderState.READY_FOR_PICKUP: {OrderState.COLLECTED, OrderState.EXPIRED},
    OrderState.COLLECTED: {OrderState.CLEANUP_PENDING},
    OrderState.EXPIRED: {OrderState.CLEANUP_PENDING},
    OrderState.CANCELLED: {OrderState.CLEANUP_PENDING},
    OrderState.CLEANUP_PENDING: {OrderState.CLEANED},
    OrderState.CLEANED: set(),
}


async def transition_order(
    session: AsyncSession,
    order: Order,
    target_state: OrderState,
    actor_type: str,
    actor_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Order:
    """
    Centralized atomic order state transition with validation and audit logging.
    Enforces the single source of truth rules from PLAN.md §10.
    """
    current_state = OrderState(order.status)

    # Validate transition
    allowed = LEGAL_TRANSITIONS.get(current_state, set())
    if target_state not in allowed:
        error_msg = f"Illegal order state transition from {current_state.value} to {target_state.value}"
        logger.error(error_msg, extra={"order_id": order.id, "error_code": ErrorCode.INVALID_STATE_TRANSITION.value})
        raise AppException(
            code=ErrorCode.INVALID_STATE_TRANSITION,
            message=error_msg,
            details={"current_state": current_state.value, "target_state": target_state.value},
        )

    # Perform transition
    order.status = target_state.value
    now = datetime.now(timezone.utc)

    if target_state == OrderState.PAID:
        order.paid_at = now
    elif target_state == OrderState.PRINTED:
        order.printed_at = now
    elif target_state == OrderState.COLLECTED:
        order.collected_at = now

    # Record Audit Log
    audit = AuditLog(
        order_id=order.id,
        event_type=f"STATE_TRANSITION_{target_state.value}",
        actor_type=actor_type,
        actor_id=actor_id,
        metadata_json=json.dumps({
            "from_state": current_state.value,
            "to_state": target_state.value,
            "extra": metadata or {},
        }),
    )
    session.add(audit)
    await session.flush()

    logger.info(
        f"Order {order.order_code} transitioned: {current_state.value} -> {target_state.value}",
        extra={"order_id": order.id},
    )
    return order
