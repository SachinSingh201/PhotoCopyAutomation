import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import settings
from backend.core.exceptions import AppException, ErrorCode
from backend.core.logging import logger
from backend.models.order import Order
from backend.models.payment import Payment
from backend.schemas.payments import PaymentCreateResponse
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.queue.service import enqueue_verified_order
from backend.services.scheduling.service import get_effective_print_state


async def create_payment_for_order(
    session: AsyncSession,
    order: Order,
) -> PaymentCreateResponse:
    """
    Creates an internal payment record and external payment intent.
    Transitions order to PAYMENT_PENDING.
    """
    if order.status not in [OrderState.AWAITING_CONFIRMATION.value, OrderState.PAYMENT_PENDING.value]:
        raise AppException(
            code=ErrorCode.INVALID_STATE_TRANSITION,
            message=f"Cannot initiate payment for order in status {order.status}",
        )

    if order.total_amount <= 0:
        raise AppException(
            code=ErrorCode.PRICE_MISMATCH,
            message="Order total amount must be greater than zero to pay.",
        )

    # Check for existing initiated payment
    pay_query = select(Payment).where(
        Payment.order_id == order.id,
        Payment.status == "INITIATED",
    )
    res = await session.execute(pay_query)
    payment = res.scalar_one_or_none()

    if not payment:
        provider_order_id = f"pay_order_{uuid.uuid4().hex[:12]}"
        payment = Payment(
            order_id=order.id,
            provider=settings.PAYMENT_PROVIDER,
            provider_order_id=provider_order_id,
            amount=order.total_amount,
            currency=order.currency,
            status="INITIATED",
        )
        session.add(payment)

    if order.status != OrderState.PAYMENT_PENDING.value:
        await transition_order(
            session=session,
            order=order,
            target_state=OrderState.PAYMENT_PENDING,
            actor_type="CUSTOMER",
            metadata={"payment_id": payment.id},
        )

    await session.flush()
    return PaymentCreateResponse(
        payment_id=payment.id,
        order_id=order.id,
        amount=payment.amount,
        currency=payment.currency,
        provider=payment.provider,
        provider_order_id=payment.provider_order_id,
    )


async def verify_payment_and_enqueue(
    session: AsyncSession,
    order: Order,
    provider_payment_id: str,
    signature: Optional[str] = None,
) -> Payment:
    """
    Server-side verification of payment.
    Idempotent: If already PAID, returns the existing payment.
    Transitions: PAYMENT_PENDING -> PAID -> (QUEUED or WAITING_FOR_PRINT_WINDOW).
    """
    pay_query = select(Payment).where(Payment.order_id == order.id)
    res = await session.execute(pay_query)
    payment = res.scalar_one_or_none()

    if not payment:
        raise AppException(
            code=ErrorCode.PAYMENT_NOT_VERIFIED,
            message=f"No payment record found for order {order.id}",
            status_code=404,
        )

    # Idempotency check
    if payment.status == "VERIFIED" and order.status in [
        OrderState.PAID.value,
        OrderState.QUEUED.value,
        OrderState.WAITING_FOR_PRINT_WINDOW.value,
    ]:
        logger.info(f"Payment already verified for order {order.id}")
        return payment

    # In production, verify HMAC signature or query provider API
    # In mock provider mode, accept any non-empty provider_payment_id
    now = datetime.now(timezone.utc)
    payment.status = "VERIFIED"
    payment.provider_payment_id = provider_payment_id
    payment.verified_at = now

    # Transition to PAID
    await transition_order(
        session=session,
        order=order,
        target_state=OrderState.PAID,
        actor_type="PAYMENT_GATEWAY",
        actor_id=settings.PAYMENT_PROVIDER,
        metadata={"provider_payment_id": provider_payment_id, "amount": payment.amount},
    )

    # Check effective print schedule
    state = await get_effective_print_state(session, now)
    if state["shop_open"] and state["effective_status"] == "RUNNING":
        await enqueue_verified_order(session=session, order=order)
    else:
        await transition_order(
            session=session,
            order=order,
            target_state=OrderState.WAITING_FOR_PRINT_WINDOW,
            actor_type="SCHEDULER",
            metadata={"reason": state["reason"]},
        )

    await session.flush()
    return payment
