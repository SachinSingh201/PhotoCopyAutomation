from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import settings
from backend.core.database import get_db
from backend.schemas.payments import PaymentCreateRequest, PaymentCreateResponse, PaymentVerifyRequest
from backend.services.orders.service import get_order_by_id
from backend.services.payments.service import create_payment_for_order, verify_payment_and_enqueue
from backend.services.payments.upi import (
    generate_qr_code_url,
    generate_upi_uri,
    validate_qr_payload,
    validate_upi_id,
)

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


@router.get("/order/{order_id}/qr")
async def get_order_payment_qr(order_id: str, session: AsyncSession = Depends(get_db)):
    """Returns dynamic NPCI UPI URI and QR code URL for any order."""
    order = await get_order_by_id(session, order_id)
    upi_uri = generate_upi_uri(
        upi_id=settings.SHOP_UPI_ID,
        payee_name=settings.SHOP_NAME,
        amount=order.total_amount,
        order_code=order.order_code,
        pickup_token=order.pickup_token,
    )
    qr_url = generate_qr_code_url(upi_uri, size=350)
    return {
        "order_id": order.id,
        "order_code": order.order_code,
        "pickup_token": order.pickup_token,
        "amount": order.total_amount,
        "currency": order.currency,
        "shop_upi_id": settings.SHOP_UPI_ID,
        "shop_name": settings.SHOP_NAME,
        "upi_uri": upi_uri,
        "qr_code_url": qr_url,
    }


@router.get("/validate-qr")
async def check_qr_validity(payload: str = Query(..., min_length=1)):
    """Validates whether a QR code payload or UPI deep link is valid."""
    return validate_qr_payload(payload)

