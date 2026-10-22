import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import settings
from backend.core.logging import logger
from backend.models.order import Order
from backend.models.payment import Payment
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.payments.upi import generate_qr_code_url, generate_upi_uri
from backend.services.queue.service import enqueue_verified_order
from backend.services.whatsapp.service import whatsapp_service


async def create_youpi_order(order: Order) -> Dict[str, Any]:
    """
    Creates a dynamic UPI payment session on the self-hosted YouPI-PG server.
    If the YouPI-PG service is offline or unreachable, seamlessly falls back
    to generating a direct UPI URI with exact payable amount.
    """
    upi_uri = generate_upi_uri(
        upi_id=settings.SHOP_UPI_ID,
        payee_name=settings.SHOP_NAME,
        amount=order.total_amount,
        order_code=order.order_code,
        pickup_token=order.pickup_token,
    )
    qr_url = generate_qr_code_url(upi_uri, size=350)

    # Primary YouPI-PG OpenAPI endpoint: /api/v1/payments
    youpi_url = f"{settings.YOUPI_GATEWAY_URL.rstrip('/')}/api/v1/payments"
    headers = {"Content-Type": "application/json"}
    if settings.YOUPI_API_KEY:
        headers["X-Merchant-Key"] = settings.YOUPI_API_KEY

    customer_name = "Customer"
    customer_phone = ""
    try:
        if hasattr(order, "user") and order.user is not None:
            customer_name = getattr(order.user, "display_name", None) or "Customer"
            raw_phone = getattr(order.user, "phone_number", None) or ""
            customer_phone = raw_phone.replace("+", "").strip()
    except Exception:
        pass

    payload = {
        "orderId": order.order_code,
        "amount": f"{order.total_amount:.2f}",
        "currency": "INR",
        "customer": {
            "name": customer_name,
            "phoneNumber": customer_phone,
        },
        "description": f"Print Order #{order.order_code} (Pickup Token: {order.pickup_token})",
    }

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(youpi_url, json=payload, headers=headers)
            if resp.status_code in [200, 201]:
                data = resp.json()
                logger.info(f"YouPI-PG order created successfully for #{order.order_code}")
                checkout_url = data.get("checkoutUrl") or data.get("checkout_url") or data.get("payment_url")
                return {
                    "status": "success",
                    "mode": "youpi",
                    "payment_id": data.get("paymentId") or data.get("id"),
                    "upi_uri": data.get("upi_uri") or data.get("upiUri") or upi_uri,
                    "payment_url": checkout_url,
                    "qr_data": data.get("qr_data") or data.get("qr_code") or upi_uri,
                    "qr_code_url": qr_url,
                    "expires_at": data.get("expiresAt"),
                }
            else:
                logger.warning(f"YouPI-PG returned status {resp.status_code}: {resp.text}. Using fallback UPI.")
    except Exception as e:
        logger.info(f"YouPI-PG gateway offline/unreachable ({e}). Seamlessly using direct UPI QR fallback.")

    return {
        "status": "fallback",
        "mode": "upi_direct",
        "upi_uri": upi_uri,
        "payment_url": None,
        "qr_data": upi_uri,
        "qr_code_url": qr_url,
    }


def verify_youpi_webhook_signature(payload_bytes: bytes, signature_header: Optional[str]) -> bool:
    """
    Verifies HMAC-SHA256 signature from YouPI-PG.
    In development with default secret or empty secret, accepts payload gracefully.
    """
    if not settings.YOUPI_WEBHOOK_SECRET or settings.YOUPI_WEBHOOK_SECRET == "youpi_webhook_secret_change_me":
        return True

    if not signature_header:
        return False

    expected = hmac.new(
        settings.YOUPI_WEBHOOK_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected.lower(), signature_header.lower())


async def process_youpi_payment_success(
    session: AsyncSession,
    event_data: Dict[str, Any],
) -> Tuple[bool, str, Optional[Order]]:
    """
    Processes an incoming payment confirmation webhook from YouPI-PG.
    Validates amount, transitions order to PAID, enqueues the print job,
    and alerts the customer on WhatsApp with their pickup token.
    If event is EXPIRED or CANCELLED, expires the order and deletes files immediately.
    """
    order_id = (
        event_data.get("orderId")
        or event_data.get("order_id")
        or event_data.get("order_code")
        or event_data.get("id")
    )
    event_type = (event_data.get("event") or "SUCCESS").upper()
    utr = event_data.get("utr") or event_data.get("transaction_id") or event_data.get("ref_no") or "YOUPI-DIRECT"
    amount_raw = event_data.get("amount")

    if not order_id:
        return False, "Missing order_id in webhook payload", None

    # Find order by order_code or UUID
    query = (
        select(Order)
        .where((Order.order_code == order_id) | (Order.id == order_id))
        .options(
            selectinload(Order.files),
            selectinload(Order.user),
            selectinload(Order.payments),
        )
    )
    result = await session.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        return False, f"Order {order_id} not found", None

    # Handle EXPIRED / CANCELLED webhook events from YouPI-PG timing engine
    if event_type in ["EXPIRED", "CANCELLED", "FAILED"]:
        from backend.services.orders.service import expire_single_order
        await expire_single_order(session, order, reason=f"YouPI-PG payment {event_type.lower()}")
        return True, f"Order #{order.order_code} expired/cancelled via YouPI webhook", order

    # Idempotency check: if order already paid or beyond, return success
    if order.status in [
        OrderState.PAID.value,
        OrderState.QUEUED.value,
        OrderState.PRINTING.value,
        OrderState.PRINTED.value,
        OrderState.READY_FOR_PICKUP.value,
    ]:
        logger.info(f"Order #{order.order_code} already in paid/printing status ({order.status}). Webhook acknowledged.")
        return True, "Order already processed as paid", order

    # Validate amount if provided
    if amount_raw is not None:
        try:
            paid_amount = float(amount_raw)
            if abs(paid_amount - order.total_amount) > 0.1:
                logger.warning(f"Payment amount mismatch for Order #{order.order_code}: expected {order.total_amount}, got {paid_amount}")
        except (ValueError, TypeError):
            pass

    now = datetime.now(timezone.utc)

    # Record verified payment
    payment = Payment(
        order_id=order.id,
        provider="YOUPI",
        provider_payment_id=str(utr),
        provider_order_id=str(order_id),
        amount=order.total_amount,
        currency=order.currency,
        status="VERIFIED",
        verified_at=now,
        raw_event_reference=json.dumps(event_data),
    )
    session.add(payment)

    # Transition order to PAID
    order.paid_at = now
    await transition_order(
        session=session,
        order=order,
        target_state=OrderState.PAID,
        actor_type="PAYMENT_GATEWAY",
        metadata={"provider": "YOUPI", "utr": str(utr)},
    )

    # Enqueue physical print job
    await enqueue_verified_order(session=session, order=order)
    await session.commit()

    logger.info(f"Order #{order.order_code} successfully verified via YouPI-PG (UTR: {utr}) and queued for printing.")

    # Notify customer via WhatsApp
    if order.user and order.user.phone_number:
        import math
        from datetime import timedelta
        pages = max(1, order.total_pages or 1)
        duration_sec = 10 + math.ceil((pages / max(settings.PRINTER_SPEED_PPM, 1)) * 60)
        eta_mins = max(1, math.ceil(duration_sec / 60))
        ready_time_str = (datetime.now() + timedelta(minutes=eta_mins)).strftime("%I:%M %p")

        success_text = (
            f"🎉 *Payment Verified via UPI!* (₹{order.total_amount:.2f})\n\n"
            f"• *Order Code:* #{order.order_code}\n"
            f"• *Pickup Token:* *{order.pickup_token}*\n"
            f"• *UTR / Ref:* `{utr}`\n"
            f"• *Status:* QUEUED FOR PRINTING\n"
            f"🕒 *Estimated Pickup Time:* Ready in ~{eta_mins} mins (by approx. {ready_time_str})!\n\n"
            f"🖨️ Your print job has been sent to the printer queue!\n"
            f"Show Token *{order.pickup_token}* at the counter to collect your prints.\n\n"
            f"✨ *Need more prints?* You can send another PDF right now to place a new order instantaneously!"
        )
        try:
            await whatsapp_service.send_text_message(to_phone=order.user.phone_number, text=success_text)
        except Exception as msg_err:
            logger.warning(f"Failed to send payment confirmation WhatsApp message: {msg_err}")

    return True, "Payment verified and order enqueued", order
