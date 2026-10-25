from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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


@router.post("/youpi/webhook")
async def youpi_payment_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """
    Webhook receiver for self-hosted YouPI-PG UPI gateway.
    Verifies HMAC signature, transitions order to PAID, and auto-dispatches print job.
    """
    import json
    from backend.services.payments.youpi import (
        process_youpi_payment_success,
        verify_youpi_webhook_signature,
    )

    body_bytes = await request.body()
    sig = request.headers.get("x-youpi-signature") or request.headers.get("x-signature")
    if not verify_youpi_webhook_signature(body_bytes, sig):
        raise HTTPException(status_code=401, detail="Invalid YouPI signature")

    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON payload")

    success, message, order = await process_youpi_payment_success(session, payload)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {
        "status": "success",
        "message": message,
        "order_code": order.order_code if order else None,
        "pickup_token": order.pickup_token if order else None,
    }


# In-memory status tracker for Counter Phone / MacroDroid observer
_phone_observer_state = {
    "status": "STANDBY",
    "last_seen_at": None,
    "last_package": None,
    "last_amount": None,
    "last_utr": None,
    "ping_count": 0,
    "event_count": 0,
}


@router.post("/phone-heartbeat")
async def phone_heartbeat_intake(request: Request):
    """Liveness heartbeat ping received from MacroDroid / counter phone."""
    from datetime import datetime, timezone
    try:
        data = await request.json()
    except Exception:
        data = {}

    now_iso = datetime.now(timezone.utc).isoformat()
    _phone_observer_state["status"] = "ONLINE"
    _phone_observer_state["last_seen_at"] = now_iso
    _phone_observer_state["ping_count"] += 1
    if data.get("package"):
        _phone_observer_state["last_package"] = data.get("package")

    return {
        "status": "ok",
        "acknowledged_at": now_iso,
        "ping_count": _phone_observer_state["ping_count"],
    }


@router.get("/phone-status")
async def get_phone_observer_status():
    """Returns current liveness status of the counter phone observer."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    is_live = False
    if _phone_observer_state["last_seen_at"]:
        try:
            last_dt = datetime.fromisoformat(_phone_observer_state["last_seen_at"].replace("Z", "+00:00"))
            # Considered online if pinged within the last 5 minutes
            if (now - last_dt).total_seconds() < 300:
                is_live = True
        except Exception:
            pass

    return {
        "status": "ONLINE" if is_live else "STANDBY",
        "is_connected": is_live,
        "connected": is_live,
        "total_events_received": _phone_observer_state["event_count"],
        "last_seen_at": _phone_observer_state["last_seen_at"],
        "last_package": _phone_observer_state["last_package"],
        "last_amount": _phone_observer_state["last_amount"],
        "last_utr": _phone_observer_state["last_utr"],
        "ping_count": _phone_observer_state["ping_count"],
        "event_count": _phone_observer_state["event_count"],
    }


@router.post("/phone-event")
async def phone_notification_event(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """
    Direct notification observer intake endpoint for MacroDroid / Tasker / Android observers.
    Extracts amount and 12-digit UTR, matches active order, marks PAID, and triggers print spooling.
    """
    import json
    import re
    from datetime import datetime, timezone
    from sqlalchemy import desc, select
    from sqlalchemy.orm import selectinload
    from backend.models.order import Order
    from backend.models.payment import Payment
    from backend.models.user import User
    from backend.services.orders.state_machine import OrderState, transition_order
    from backend.services.queue.service import enqueue_verified_order
    from backend.services.whatsapp.bot import get_order_pickup_timing
    from backend.services.whatsapp.service import whatsapp_service
    from backend.core.logging import logger

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON payload")

    raw_text = (
        body.get("text")
        or body.get("rawNotification", {}).get("text")
        or body.get("message")
        or (str(body.get("amount")) if body.get("amount") is not None else "")
    )
    title = body.get("title") or body.get("rawNotification", {}).get("title") or ""
    pkg = body.get("package") or body.get("rawNotification", {}).get("package") or "com.phonepe.app"

    full_text = f"{title} {raw_text}".strip()
    if not full_text:
        raise HTTPException(status_code=400, detail="Missing notification text")

    # Update liveness tracker
    now = datetime.now(timezone.utc)
    _phone_observer_state["status"] = "ONLINE"
    _phone_observer_state["last_seen_at"] = now.isoformat()
    _phone_observer_state["last_package"] = pkg
    _phone_observer_state["event_count"] += 1

    # Extract monetary amount
    extracted_amount: Optional[float] = None
    amt_match = re.search(r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)", full_text, re.IGNORECASE)
    if amt_match:
        try:
            extracted_amount = float(amt_match.group(1).replace(",", ""))
        except Exception:
            pass
    elif "amount" in body and body["amount"]:
        try:
            extracted_amount = float(re.sub(r"[^\d.]", "", str(body["amount"])))
        except Exception:
            pass

    if extracted_amount is not None:
        _phone_observer_state["last_amount"] = extracted_amount

    # Extract 12-digit UTR / reference number
    utr_match = re.search(r"(?:UPI\s*(?:ref|reference|txn|id)?|Ref(?:erence)?\s*(?:no|number)?|UTR)[:\s#=-]*([0-9]{12})\b", full_text, re.IGNORECASE)
    if not utr_match:
        utr_match = re.search(r"\b([0-9]{12})\b", full_text)
    extracted_utr = utr_match.group(1) if utr_match else f"UPI-OBS-{now.strftime('%H%M%S')}"
    _phone_observer_state["last_utr"] = extracted_utr

    # Find the most relevant order waiting for payment
    query = (
        select(Order)
        .where(Order.status == OrderState.PAYMENT_PENDING.value)
        .options(selectinload(Order.user), selectinload(Order.files))
        .order_by(desc(Order.created_at))
    )
    res = await session.execute(query)
    pending_orders = res.scalars().all()

    matched_order: Optional[Order] = None
    if extracted_amount is not None:
        # Match by exact or close amount (within 50 paise)
        for ord_candidate in pending_orders:
            if abs(ord_candidate.total_amount - extracted_amount) <= 0.50:
                matched_order = ord_candidate
                break

    # If no exact amount match found, take the most recent pending order
    if not matched_order and pending_orders:
        matched_order = pending_orders[0]

    if not matched_order:
        logger.info(f"Phone observer received alert for ₹{extracted_amount} (UTR: {extracted_utr}), but no active PAYMENT_PENDING order was found.")
        return {
            "status": "success",
            "action": "recorded_without_order",
            "reason": "NO_PENDING_ORDER",
            "amount": extracted_amount,
            "utr": extracted_utr,
            "message": "Payment notification received and registered on server.",
            "phone_liveness": _phone_observer_state["status"]
        }

    # Record Payment & Transition Order
    payment = Payment(
        order_id=matched_order.id,
        provider="PHONE_OBSERVER",
        provider_payment_id=str(extracted_utr),
        provider_order_id=str(matched_order.order_code),
        amount=matched_order.total_amount,
        currency=matched_order.currency,
        status="VERIFIED",
        verified_at=now,
        raw_event_reference=json.dumps({"package": pkg, "raw_text": full_text}),
    )
    session.add(payment)

    matched_order.paid_at = now
    await transition_order(
        session=session,
        order=matched_order,
        target_state=OrderState.PAID,
        actor_type="PHONE_OBSERVER",
        metadata={"provider": "PHONE_OBSERVER", "utr": str(extracted_utr), "package": pkg},
    )

    await enqueue_verified_order(session=session, order=matched_order)
    await session.commit()

    logger.info(f"Order #{matched_order.order_code} verified via Phone Notification (App: {pkg}, UTR: {extracted_utr}) and queued for printing.")

    # Send WhatsApp confirmation to customer
    if matched_order.user and matched_order.user.phone_number:
        try:
            timing = get_order_pickup_timing(matched_order, settings.PRINTER_SPEED_PPM)
            confirm_msg = (
                f"✅ *Payment Confirmed!* (Order #{matched_order.order_code})\n\n"
                f"• *Status:* QUEUED FOR PRINTING\n"
                f"• *Amount Paid:* ₹{matched_order.total_amount:.2f}\n"
                f"• *Pickup Token:* *{matched_order.pickup_token}*\n"
                f"• *Payment Ref / UTR:* `{extracted_utr}`\n"
                f"🕒 *Estimated Pickup Time:* Ready in ~{timing['eta_mins']} mins (by approx. {timing['ready_time_str']})!\n\n"
                f"🖨️ Your document has been sent to the print queue! Show Token *{matched_order.pickup_token}* at the counter to collect your prints."
            )
            await whatsapp_service.send_text_message(to_phone=matched_order.user.phone_number, text=confirm_msg)
        except Exception as wex:
            logger.warning(f"Could not dispatch WhatsApp confirmation message: {wex}")

    return {
        "status": "success",
        "order_code": matched_order.order_code,
        "pickup_token": matched_order.pickup_token,
        "amount_verified": matched_order.total_amount,
        "utr": extracted_utr,
        "package": pkg,
    }


@router.post("/razorpay/webhook")
async def razorpay_payment_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """
    Direct cloud-to-server webhook receiver for Razorpay payments.
    Verifies cryptographic webhook signature, transitions order to PAID, and queues printing.
    """
    import hmac
    import hashlib
    import json
    from datetime import datetime, timezone
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from backend.models.order import Order
    from backend.models.payment import Payment
    from backend.services.orders.state_machine import OrderState, transition_order
    from backend.services.queue.service import enqueue_verified_order
    from backend.services.whatsapp.bot import get_order_pickup_timing
    from backend.services.whatsapp.service import whatsapp_service
    from backend.core.logging import logger

    body_bytes = await request.body()
    secret = (settings.PAYMENT_WEBHOOK_SECRET or settings.PAYMENT_KEY_SECRET or "").encode("utf-8")
    sig = request.headers.get("x-razorpay-signature", "")

    if secret and sig:
        expected = hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.lower(), sig.lower()):
            raise HTTPException(status_code=401, detail="Invalid Razorpay webhook signature")

    try:
        event_data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON webhook body")

    event_type = event_data.get("event", "")
    payload_obj = event_data.get("payload", {})
    payment_entity = payload_obj.get("payment", {}).get("entity") or payload_obj.get("payment_link", {}).get("entity") or {}

    # Extract Order identifier from notes or description
    notes = payment_entity.get("notes") or {}
    order_code = (
        notes.get("order_code")
        or notes.get("order_id")
        or payment_entity.get("description", "").replace("Print Order #", "").split(" ")[0].replace("#", "")
    )
    utr = payment_entity.get("acquirer_data", {}).get("rrn") or payment_entity.get("acquirer_data", {}).get("upi_transaction_id") or payment_entity.get("id") or "RZP-PAYMENT"

    if not order_code:
        return {"status": "ignored", "reason": "No order_code found in payment entity"}

    query = (
        select(Order)
        .where((Order.order_code == order_code) | (Order.id == order_code))
        .options(selectinload(Order.user), selectinload(Order.files))
    )
    res = await session.execute(query)
    order = res.scalar_one_or_none()

    if not order:
        return {"status": "ignored", "reason": f"Order {order_code} not found"}

    if order.status in [OrderState.PAID.value, OrderState.QUEUED.value, OrderState.PRINTING.value, OrderState.PRINTED.value]:
        return {"status": "acknowledged", "message": "Order already processed"}

    now = datetime.now(timezone.utc)
    payment = Payment(
        order_id=order.id,
        provider="RAZORPAY",
        provider_payment_id=str(utr),
        provider_order_id=str(payment_entity.get("id", order.order_code)),
        amount=order.total_amount,
        currency=order.currency,
        status="VERIFIED",
        verified_at=now,
        raw_event_reference=json.dumps(event_data),
    )
    session.add(payment)

    order.paid_at = now
    await transition_order(
        session=session,
        order=order,
        target_state=OrderState.PAID,
        actor_type="PAYMENT_GATEWAY",
        metadata={"provider": "RAZORPAY", "utr": str(utr)},
    )

    await enqueue_verified_order(session=session, order=order)
    await session.commit()

    logger.info(f"Order #{order.order_code} verified via Razorpay Cloud Webhook (UTR: {utr}) and queued for printing.")

    # Notify WhatsApp
    if order.user and order.user.phone_number:
        try:
            timing = get_order_pickup_timing(order, settings.PRINTER_SPEED_PPM)
            confirm_msg = (
                f"✅ *Payment Confirmed!* (Order #{order.order_code})\n\n"
                f"• *Status:* QUEUED FOR PRINTING\n"
                f"• *Amount Paid:* ₹{order.total_amount:.2f}\n"
                f"• *Pickup Token:* *{order.pickup_token}*\n\n"
                f"🖨️ Your document has been sent to the print queue! Show Token *{order.pickup_token}* at the counter to collect your prints."
            )
            await whatsapp_service.send_text_message(to_phone=order.user.phone_number, text=confirm_msg)
        except Exception:
            pass

    return {
        "status": "success",
        "order_code": order.order_code,
        "pickup_token": order.pickup_token,
        "utr": utr,
    }


