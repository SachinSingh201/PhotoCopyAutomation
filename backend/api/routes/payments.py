from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.database import get_db
from backend.schemas.payments import PaymentCreateRequest, PaymentCreateResponse, PaymentVerifyRequest
from backend.services.orders.service import get_order_by_id
from backend.services.payments.service import create_payment_for_order, verify_payment_and_enqueue

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("/create", response_model=PaymentCreateResponse)
async def initiate_payment(payload: PaymentCreateRequest, session: AsyncSession = Depends(get_db)):
    order = await get_order_by_id(session, payload.order_id)
    payment = await create_payment_for_order(session, order)
    await session.commit()
    return payment


@router.post("/verify")
async def verify_payment(payload: PaymentVerifyRequest, session: AsyncSession = Depends(get_db)):
    order = await get_order_by_id(session, payload.order_id)
    payment = await verify_payment_and_enqueue(
        session=session,
        order=order,
        provider_payment_id=payload.provider_payment_id,
        signature=payload.signature,
    )
    await session.commit()
    return {
        "status": "success",
        "order_id": order.id,
        "payment_status": payment.status,
        "order_status": order.status,
        "pickup_token": order.pickup_token,
    }
