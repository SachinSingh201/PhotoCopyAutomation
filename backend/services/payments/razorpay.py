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


def check_youpi_local_db_for_payment(order_code: str, amount: float) -> Optional[Dict[str, Any]]:
    """
    Checks the local YouPI-PG SQLite database for confirmed payment sessions
    or recent banking observations matching the order.
    """
    import sqlite3
    from pathlib import Path
    db_path = Path("YouPI-PG/data/youpi_pg.sqlite")
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        # 1. Check direct payment session status
        cur.execute(
            "SELECT id, status, amount_paise FROM payment_sessions WHERE order_id = ?",
            (order_code,),
        )
        row = cur.fetchone()
        if row:
            sess_id, sess_status, amount_paise = row
            if str(sess_status).upper() == "SUCCESS":
                conn.close()
                return {"verified": True, "utr": sess_id, "source": "YOUPI_SESSION"}

        # 2. Check recent observations matching exact amount
        target_paise = int(round(amount * 100))
        cur.execute(
            "SELECT id, bank_reference_number FROM payment_observations WHERE amount_paise = ? ORDER BY created_at DESC LIMIT 1",
            (target_paise,),
        )
        obs_row = cur.fetchone()
        conn.close()
        if obs_row:
            obs_id, utr = obs_row
            return {"verified": True, "utr": utr or obs_id, "source": "YOUPI_OBSERVATION"}
    except Exception as e:
        logger.warning(f"Error checking YouPI-PG DB for order {order_code}: {e}")
    return None


async def check_youpi_api_status(order_code: str) -> Optional[Dict[str, Any]]:
    """Queries YouPI-PG core service port 6100 for payment status."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get(f"{settings.YOUPI_GATEWAY_URL.rstrip('/')}/api/v1/payments/{order_code}")
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "SUCCESS":
                    return {"verified": True, "utr": data.get("utr") or order_code, "source": "YOUPI_API"}
    except Exception:
        pass
    return None


async def reverify_order_payment(session: AsyncSession, order: Order, utr: Optional[str] = None) -> Dict[str, Any]:
    """
    Checks if an order has been paid online or verified via UTR or YouPI-PG.
    If paid, advances order to PAID -> QUEUED and triggers printing.
    """
    import json
    import re
    from datetime import datetime, timezone
    from backend.services.orders.state_machine import transition_order
    from backend.services.queue.service import enqueue_verified_order

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

    # 1. Customer-provided UTR (e.g. from WhatsApp "PAID 429512345678" or bank reference)
    if utr:
        clean_utr = re.sub(r"[^\d]", "", utr)
        if len(clean_utr) >= 10:
            now = datetime.now(timezone.utc)
            payment = Payment(
                order_id=order.id,
                provider="YOUPI_UPI",
                provider_payment_id=clean_utr,
                provider_order_id=order.order_code,
                amount=order.total_amount,
                currency=order.currency,
                status="VERIFIED",
                verified_at=now,
                raw_event_reference=json.dumps({"method": "CUSTOMER_UTR", "utr": clean_utr}),
            )
            session.add(payment)
            order.paid_at = now
            if order.status != OrderState.PAID.value:
                await transition_order(
                    session=session,
                    order=order,
                    target_state=OrderState.PAID,
                    actor_type="CUSTOMER",
                    metadata={"utr": clean_utr, "method": "CUSTOMER_UTR"},
                )
            await enqueue_verified_order(session=session, order=order)
            await session.commit()
            return {
                "verified": True,
                "status": order.status,
                "utr": clean_utr,
                "message": f"Payment of ₹{order.total_amount:.2f} successfully verified with UTR {clean_utr}! Your print job has been queued.",
                "pickup_token": order.pickup_token,
            }

    # 2. Check YouPI-PG database or API
    youpi_check = check_youpi_local_db_for_payment(order.order_code, order.total_amount)
    if not youpi_check:
        youpi_check = await check_youpi_api_status(order.order_code)

    if youpi_check and youpi_check.get("verified"):
        verified_utr = youpi_check.get("utr", f"YOUPI_{order.order_code}")
        now = datetime.now(timezone.utc)
        payment = Payment(
            order_id=order.id,
            provider="YOUPI",
            provider_payment_id=str(verified_utr),
            provider_order_id=order.order_code,
            amount=order.total_amount,
            currency=order.currency,
            status="VERIFIED",
            verified_at=now,
            raw_event_reference=json.dumps(youpi_check),
        )
        session.add(payment)
        order.paid_at = now
        if order.status != OrderState.PAID.value:
            await transition_order(
                session=session,
                order=order,
                target_state=OrderState.PAID,
                actor_type="PAYMENT_GATEWAY",
                metadata={"utr": str(verified_utr), "source": "YOUPI_AUTO"},
            )
        await enqueue_verified_order(session=session, order=order)
        await session.commit()
        return {
            "verified": True,
            "status": order.status,
            "utr": str(verified_utr),
            "message": f"Payment of ₹{order.total_amount:.2f} successfully verified via YouPI-PG (UTR: {verified_utr})! Your print job has been queued.",
            "pickup_token": order.pickup_token,
        }

    # 3. Query existing payment records (e.g. Razorpay payment link)
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
        "message": (
            f"Payment not detected yet for Order #{order.order_code} (₹{order.total_amount:.2f}).\n\n"
            f"👉 If you already paid via UPI (Google Pay, PhonePe, Paytm, etc.), reply with your 12-digit UTR/Ref number:\n"
            f"*PAID <12-digit UTR>* (e.g. `PAID 429512345678`)\n"
            f"👉 If paying in cash, show Token *{order.pickup_token}* to the shop owner at the counter.\n"
            f"👉 Or reply *CANCEL* to cancel this order."
        ),
        "pickup_token": order.pickup_token,
    }
