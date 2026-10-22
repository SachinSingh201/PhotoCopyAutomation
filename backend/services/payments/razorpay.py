import base64
from typing import Any, Dict, Optional
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import settings
from backend.core.logging import logger
from backend.models.order import Order
from backend.models.payment import Payment
from backend.services.orders.state_machine import OrderState
from backend.services.payments.service import verify_payment_and_enqueue


def _get_razorpay_auth_header() -> Optional[Dict[str, str]]:
    key_id = (settings.PAYMENT_KEY_ID or "").strip()
    key_secret = (settings.PAYMENT_KEY_SECRET or "").strip()

    if not key_id or not key_secret or key_id in ["mock_key_id", ""] or key_secret in ["mock_key_secret", ""]:
        return None

    credentials = f"{key_id}:{key_secret}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}


async def create_razorpay_payment_link(order: Order) -> Optional[Dict[str, Any]]:
    """
    Creates an official Razorpay Standard Payment Link with UPI intent, Cards & NetBanking.
    Returns short_url (e.g. https://rzp.io/i/xxxxxx) to send directly to customer on WhatsApp.
    """
    headers = _get_razorpay_auth_header()
    if not headers:
        # Mock mode or unconfigured keys
        return {
            "plink_id": f"plink_mock_{order.order_code}",
            "short_url": f"https://rzp.io/i/mock_{order.order_code}",
            "amount": order.total_amount,
            "mock": True,
        }

    amount_paise = int(round(order.total_amount * 100))
    if amount_paise <= 0:
        return None

    cust_name = "Customer"
    cust_phone = ""
    if order.user:
        cust_name = order.user.display_name or "Customer"
        cust_phone = order.user.phone_number or ""

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "description": f"Print Order #{order.order_code} (Pickup Token: {order.pickup_token})",
        "customer": {
            "name": cust_name,
            "contact": cust_phone if cust_phone else None,
        },
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "notes": {
            "order_id": order.id,
            "order_code": order.order_code,
            "pickup_token": order.pickup_token or "",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post("https://api.razorpay.com/v1/payment_links", headers=headers, json=payload)
            if res.status_code in [200, 201]:
                data = res.json()
                logger.info(f"Razorpay Payment Link created: {data.get('short_url')} for Order {order.order_code}")
                return {
                    "plink_id": data.get("id"),
                    "short_url": data.get("short_url"),
                    "amount": order.total_amount,
                    "mock": False,
                }
            else:
                logger.warning(f"Razorpay API error ({res.status_code}): {res.text}")
    except Exception as e:
        logger.warning(f"Failed to connect to Razorpay API: {e}")

    return {
        "plink_id": f"plink_fallback_{order.order_code}",
        "short_url": f"https://rzp.io/i/fallback_{order.order_code}",
        "amount": order.total_amount,
        "mock": True,
    }


async def check_razorpay_payment_status(plink_id: str) -> Dict[str, Any]:
    """Queries Razorpay API to check the real-time status of a Payment Link."""
    headers = _get_razorpay_auth_header()
    if not headers or plink_id.startswith("plink_mock") or plink_id.startswith("plink_fallback"):
        return {"paid": False, "status": "mock", "amount_paid": 0.0}

    url = f"https://api.razorpay.com/v1/payment_links/{plink_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                status = data.get("status", "").lower()
                paid = status == "paid"
                amount_paid = float(data.get("amount_paid", 0)) / 100.0
                return {
                    "paid": paid,
                    "status": status,
                    "amount_paid": amount_paid,
                    "raw": data,
                }
    except Exception as e:
        logger.warning(f"Razorpay status check error for {plink_id}: {e}")

    return {"paid": False, "status": "unknown", "amount_paid": 0.0}


async def reverify_order_payment(session: AsyncSession, order: Order) -> Dict[str, Any]:
    """
    Checks if an order has been paid online or verified.
    If paid, advances order to PAID -> QUEUED and triggers printing.
    """
    if order.status in [
        OrderState.PAID.value,
        OrderState.QUEUED.value,
        OrderState.PRINTING.value,
        OrderState.PRINTED.value,
        OrderState.READY_FOR_PICKUP.value,
        OrderState.COLLECTED.value,
    ]:
        return {
            "verified": True,
            "status": order.status,
            "message": f"Payment already confirmed! Order is currently {order.status}.",
            "pickup_token": order.pickup_token,
        }

    # Query payment records
    pay_res = await session.execute(select(Payment).where(Payment.order_id == order.id))
    payment = pay_res.scalar_one_or_none()

    if payment and payment.provider_order_id and payment.provider_order_id.startswith("plink_"):
        check = await check_razorpay_payment_status(payment.provider_order_id)
        if check["paid"]:
            await verify_payment_and_enqueue(
                session=session,
                order=order,
                provider_payment_id=f"rzp_{payment.provider_order_id}",
            )
            await session.commit()
            return {
                "verified": True,
                "status": order.status,
                "message": f"Payment of ₹{order.total_amount:.2f} successfully verified via Razorpay! Your print job has been queued.",
                "pickup_token": order.pickup_token,
            }

    return {
        "verified": False,
        "status": order.status,
        "message": f"Payment not detected yet for Order #{order.order_code}. Please scan the QR code or complete payment at the counter using Token: {order.pickup_token}.",
        "pickup_token": order.pickup_token,
    }
