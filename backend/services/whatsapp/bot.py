import asyncio
import json
import math
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.core.config import settings
from backend.core.database import AsyncSessionLocal
from backend.core.logging import logger
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.models.user import ConversationSession, User
from backend.models.whatsapp_message import ProcessedWhatsAppMessage
from backend.services.files.service import process_and_save_order_file
from backend.services.llm.parser import interpret_print_instructions
from backend.services.orders.service import cleanup_expired_orders, create_order, get_or_create_user, get_order_by_id
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.payments.razorpay import create_razorpay_payment_link, reverify_order_payment
from backend.services.payments.upi import generate_qr_code_url, generate_upi_uri
from backend.services.payments.youpi import create_youpi_order
from backend.services.pricing.engine import calculate_order_pricing_snapshot
from backend.services.security import mask_phone, message_rate_limiter, upload_rate_limiter
from backend.services.whatsapp.service import whatsapp_service

_pending_doc_replies: Dict[str, asyncio.Task] = {}


def get_order_pickup_timing(order: Order, ppm: int = 20) -> Dict[str, Any]:
    """Calculates estimated print duration and expected pickup clock time."""
    pages = max(1, order.total_pages or 1)
    duration_sec = 10 + math.ceil((pages / max(ppm, 1)) * 60)
    eta_mins = max(1, math.ceil(duration_sec / 60))
    ready_time = datetime.now() + timedelta(minutes=eta_mins)
    return {
        "eta_mins": eta_mins,
        "ready_time_str": ready_time.strftime("%I:%M %p"),
    }


async def send_payment_qr(sender: str, order: Order, session: Optional[AsyncSession] = None) -> None:
    """
    Dispatches the shop counter QR code (or local dynamic UPI QR / YouPI checkout) with the exact payable amount.
    Includes the 5-minute payment expiration warning and estimated pickup timing.
    """
    youpi_info = None
    if settings.PAYMENT_PROVIDER == "youpi":
        try:
            youpi_info = await create_youpi_order(order)
        except Exception as ye:
            logger.warning(f"Could not initialize YouPI payment order: {ye}")

    rzp_url = None
    if settings.PAYMENT_PROVIDER == "razorpay":
        try:
            rzp_info = await create_razorpay_payment_link(order)
            if rzp_info:
                rzp_url = rzp_info.get("short_url")
        except Exception as e:
            logger.warning(f"Could not generate Razorpay payment link: {e}")

    timing = get_order_pickup_timing(order, settings.PRINTER_SPEED_PPM)

    # Build clear caption detailing payable amount, pickup timing, timeout, and tokens
    caption_lines = [
        f"💳 *Scan QR to Pay: ₹{order.total_amount:.2f}*",
        f"• *Shop:* {settings.SHOP_NAME}",
        f"• *UPI ID:* `{settings.SHOP_UPI_ID}`",
        f"• *Order Code:* #{order.order_code}",
        f"• *Pickup Token:* *{order.pickup_token}*",
        f"• *Total Payable:* *₹{order.total_amount:.2f}*",
        f"🕒 *Estimated Pickup Time:* ~{timing['eta_mins']} mins (Ready by ~{timing['ready_time_str']})",
        f"⏱️ *Payment Window: {settings.ORDER_PAYMENT_TIMEOUT_MINUTES} minutes* (Order will be cancelled if unpaid)",
    ]
    if youpi_info and youpi_info.get("payment_url"):
        caption_lines.extend([
            "",
            f"⚡ *Pay via YouPI Checkout:*",
            f"{youpi_info['payment_url']}",
        ])
    elif rzp_url:
        caption_lines.extend([
            "",
            f"🌐 *Or Pay via Razorpay:*",
            f"{rzp_url}",
        ])
    caption_lines.extend([
        "",
        "⚡ *After paying:* Reply *PAID <12-digit UTR>* (e.g. `PAID 429512345678`) to instantly queue your prints!",
        "ℹ️ *Where to find UTR?* Look for 'UPI Ref No.', 'UTR', or 'Transaction ID' (12 digits) on your Google Pay / PhonePe / Paytm receipt.",
    ])
    caption = "\n".join(caption_lines)

    # Determine image path: custom shop counter QR, or locally generated dynamic QR
    qr_image_path = None
    if settings.shop_qr_file.exists():
        qr_image_path = str(settings.shop_qr_file)
    else:
        try:
            import qrcode
            upi_uri = (youpi_info.get("upi_uri") if youpi_info else None) or generate_upi_uri(
                upi_id=settings.SHOP_UPI_ID,
                payee_name=settings.SHOP_NAME,
                amount=order.total_amount,
                order_code=order.order_code,
                pickup_token=order.pickup_token,
            )
            qr = qrcode.QRCode(box_size=10, border=2)
            qr.add_data(upi_uri)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            dynamic_qr_path = settings.storage_path / f"qr_{order.order_code}.png"
            img.save(str(dynamic_qr_path))
            qr_image_path = str(dynamic_qr_path)
        except Exception as gen_err:
            logger.warning(f"Could not generate local dynamic QR image: {gen_err}")

    # Send QR image
    if qr_image_path:
        try:
            await whatsapp_service.send_image(
                to_phone=sender,
                file_path_or_url=qr_image_path,
                caption=caption,
            )
            return
        except Exception as img_err:
            logger.warning(f"Sending QR image failed: {img_err}. Sending fallback text.")

    # Fallback to text message if image fails
    fallback_text = (
        f"💳 *Payment Details (Order #{order.order_code})*\n\n"
        f"• *Payable Amount:* ₹{order.total_amount:.2f}\n"
        f"• *Shop UPI ID:* `{settings.SHOP_UPI_ID}`\n"
        f"• *Pickup Token:* *{order.pickup_token}*\n"
        f"⏱️ *Payment Window: {settings.ORDER_PAYMENT_TIMEOUT_MINUTES} minutes* (Order will be cancelled if unpaid)\n"
    )
    if youpi_info and youpi_info.get("payment_url"):
        fallback_text += f"\n⚡ *Pay Online via YouPI:*\n{youpi_info['payment_url']}\n"
    elif rzp_url:
        fallback_text += f"\n🌐 *Pay Online via Razorpay:*\n{rzp_url}\n"
    fallback_text += (
        f"\n⚡ *After paying:* Reply *PAID <12-digit UTR>* (e.g. `PAID 429512345678`)\n"
        f"👉 Or show Token *{order.pickup_token}* at the counter if paying cash."
    )
    await whatsapp_service.send_text_message(to_phone=sender, text=fallback_text)


DRAFT_STATES = [
    OrderState.CREATED.value,
    OrderState.FILES_UPLOADING.value,
    OrderState.FILES_UPLOADED.value,
    OrderState.CONFIGURATION_PENDING.value,
    OrderState.CONFIGURED.value,
    OrderState.PRICE_CALCULATED.value,
    OrderState.AWAITING_CONFIRMATION.value,
]


async def get_active_draft_order(session: AsyncSession, user: User) -> Optional[Order]:
    """Retrieves customer's current open draft order (where files are still being uploaded/configured)."""
    query = (
        select(Order)
        .where(Order.user_id == user.id, Order.status.in_(DRAFT_STATES))
        .options(
            selectinload(Order.files).selectinload(OrderFile.configuration),
            selectinload(Order.user),
        )
        .order_by(desc(Order.created_at))
    )
    result = await session.execute(query)
    return result.scalars().first()


async def get_active_customer_order(session: AsyncSession, user: User) -> Optional[Order]:
    """Retrieves customer's current open/uncompleted print order, prioritizing draft then payment pending."""
    uncompleted_states = DRAFT_STATES + [OrderState.PAYMENT_PENDING.value]
    query = (
        select(Order)
        .where(Order.user_id == user.id, Order.status.in_(uncompleted_states))
        .options(
            selectinload(Order.files).selectinload(OrderFile.configuration),
            selectinload(Order.user),
        )
        .order_by(desc(Order.created_at))
    )
    result = await session.execute(query)
    return result.scalars().first()


async def get_all_active_user_orders(session: AsyncSession, user: User) -> List[Order]:
    """Retrieves all non-final active orders for a customer (for status check and management)."""
    active_states = DRAFT_STATES + [
        OrderState.PAYMENT_PENDING.value,
        OrderState.PAID.value,
        OrderState.WAITING_FOR_PRINT_WINDOW.value,
        OrderState.QUEUED.value,
        OrderState.PRINTING.value,
        OrderState.PRINTED.value,
        OrderState.READY_FOR_PICKUP.value,
    ]
    query = (
        select(Order)
        .where(Order.user_id == user.id, Order.status.in_(active_states))
        .options(
            selectinload(Order.files).selectinload(OrderFile.configuration),
            selectinload(Order.user),
        )
        .order_by(desc(Order.created_at))
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def handle_inbound_whatsapp_event(session: AsyncSession, event: Dict[str, Any]) -> None:
    """
    Main conversation orchestrator for inbound WhatsApp events (OpenWA or Meta).
    Strictly coordinates:
    1. Customer & Order resolution
    2. Document media download & verification
    3. LLM instruction parsing
    4. Deterministic mathematical pricing
    5. Direct WhatsApp reply dispatch
    """
    sender = event.get("sender")
    if not sender:
        logger.warning(f"Inbound WhatsApp event without sender: {event}")
        return

    # Webhook idempotency: prevent duplicate processing of same message
    message_id = event.get("message_id")
    provider = event.get("provider", "unknown")
    if message_id:
        check_query = select(ProcessedWhatsAppMessage).where(
            ProcessedWhatsAppMessage.message_id == message_id,
            ProcessedWhatsAppMessage.sender == sender
        )
        existing = await session.execute(check_query)
        if existing.scalar_one_or_none():
            logger.info(f"Skipping duplicate WhatsApp message {message_id} from {sender}")
            return

        # Mark message as processed immediately
        processed_msg = ProcessedWhatsAppMessage(
            message_id=message_id,
            sender=sender,
            provider=provider,
            event_type=event.get("type", "unknown"),
            processed_at=datetime.now(timezone.utc)
        )
        session.add(processed_msg)
        await session.flush()

    display_name = event.get("display_name") or "Customer"
    event_type = event.get("type", "text")

    # Inbound message rate limiter (anti-spam / anti-loop protection)
    if not message_rate_limiter.check_message(sender):
        logger.warning(f"Inbound message rate limit triggered for {mask_phone(sender)}")
        return

    # 0. Clean up any unpaid orders that have exceeded the payment window
    await cleanup_expired_orders(session)

    # 1. Get or create Customer User
    user = await get_or_create_user(
        session=session,
        whatsapp_user_id=sender,
        phone_number=sender,
        display_name=display_name,
    )

    # 2. Find active order
    order = await get_active_customer_order(session, user)

    # --------------------------------------------------------------------------
    # Case A: Document Attachment Upload (e.g. PDF)
    # --------------------------------------------------------------------------
    if event_type == "document":
        # Upload Rate Limiting: Max 50 PDF uploads within 5 minutes
        is_allowed, limit_msg = upload_rate_limiter.check_upload(sender)
        if not is_allowed:
            logger.warning(f"Upload rate limit exceeded for {mask_phone(sender)}")
            await whatsapp_service.send_text_message(to_phone=sender, text=limit_msg)
            return

        doc_info = event.get("document", {})
        filename = doc_info.get("filename") or "document.pdf"
        mime_type = doc_info.get("mime_type") or "application/pdf"

        # Check for open draft order where user can add files.
        # If previous order is already confirmed (PAYMENT_PENDING) or completed, a fresh order is created!
        order = await get_active_draft_order(session, user)
        if not order:
            created_order = await create_order(session=session, user=user)
            order = await get_order_by_id(session, created_order.id)

        # Check maximum files per order limit
        if len(order.files) >= settings.MAX_FILES_PER_ORDER:
            await whatsapp_service.send_text_message(
                to_phone=sender,
                text=(
                    f"⚠️ *File Limit Reached:* Order #{order.order_code} already contains {settings.MAX_FILES_PER_ORDER} files (maximum limit per order).\n\n"
                    f"👉 Reply *DONE* (or *CONFIRM*) to lock in your order with these files.\n"
                    f"👉 Or reply *CANCEL* to cancel this order and start over."
                ),
            )
            return

        # Download document bytes via WhatsApp Transport Adapter
        file_bytes = await whatsapp_service.download_media(doc_info)
        if not file_bytes:
            await whatsapp_service.send_text_message(
                to_phone=sender,
                text=f"⚠️ Could not download *{filename}*. Please try sending the document again.",
            )
            return

        # Save and validate file (extracts page count via PDF processor)
        order_file = await process_and_save_order_file(
            session=session,
            order=order,
            original_filename=filename,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )

        # Recalculate deterministic price snapshot
        await calculate_order_pricing_snapshot(session, order)

        # Transition order state
        if order.status == OrderState.CREATED.value:
            await transition_order(session, order, OrderState.FILES_UPLOADING, actor_type="CUSTOMER")
            await transition_order(session, order, OrderState.FILES_UPLOADED, actor_type="CUSTOMER")
        elif order.status == OrderState.FILES_UPLOADING.value:
            await transition_order(session, order, OrderState.FILES_UPLOADED, actor_type="CUSTOMER")
        elif order.status == OrderState.AWAITING_CONFIRMATION.value:
            await transition_order(session, order, OrderState.CONFIGURATION_PENDING, actor_type="CUSTOMER")
        await session.commit()

        # Reload order to reflect fresh state
        order = await get_order_by_id(session, order.id)

        # Concise file confirmation with estimated print time
        timing = get_order_pickup_timing(order, settings.PRINTER_SPEED_PPM)
        reply = (
            f"📄 *File {len(order.files)} of {settings.MAX_FILES_PER_ORDER} Received!*\n"
            f"• *File:* {order_file.original_filename} ({order_file.page_count} pages)\n"
            f"• *Files in this Order:* {len(order.files)} files ({order.total_pages} pages total)\n"
            f"• *Estimated Print Time:* ~{timing['eta_mins']} mins\n\n"
            f"👉 Send another PDF to add more files to this order (up to {settings.MAX_FILES_PER_ORDER}).\n"
            f"👉 Reply *DONE* (or *CONFIRM*) once you have sent all your files to get the final bill & payment QR!\n"
            f"👉 Or reply *CANCEL* to discard this order."
        )
        await whatsapp_service.send_text_message(to_phone=sender, text=reply)
        return

    # --------------------------------------------------------------------------
    # Case B: Interactive Button or Action
    # --------------------------------------------------------------------------
    if event_type == "interactive":
        btn = event.get("button", {})
        btn_id = btn.get("id", "").upper()
        if "CONFIRM" in btn_id:
            event_type = "text"
            event["text"] = "CONFIRM"

    # --------------------------------------------------------------------------
    # Case C: Text Message (Instruction, Command, or Confirmation)
    # --------------------------------------------------------------------------
    if event_type == "text":
        user_text = event.get("text", "").strip()
        cmd = user_text.upper()

        # Subcase C1: Cancellation command
        if cmd in ["CANCEL", "CANCEL ORDER", "CANCEL PRINT", "ABORT", "RESET"]:
            active_orders = await get_all_active_user_orders(session, user)
            cancellable = [
                o for o in active_orders 
                if o.status in DRAFT_STATES + [
                    OrderState.PAYMENT_PENDING.value,
                    OrderState.WAITING_FOR_PRINT_WINDOW.value,
                    OrderState.QUEUED.value,
                ]
            ]
            if not cancellable:
                await whatsapp_service.send_text_message(
                    to_phone=sender,
                    text="ℹ️ You have no active orders to cancel. Send a PDF document to start a new order anytime!",
                )
                return

            target_order = cancellable[0]
            await transition_order(session, target_order, OrderState.CANCELLED, actor_type="CUSTOMER", metadata={"reason": "Cancelled by customer via WhatsApp"})
            await session.commit()

            reply = (
                f"❌ *Order #{target_order.order_code} (Token: {target_order.pickup_token}) has been cancelled.*\n\n"
                f"• *Files Discarded:* {len(target_order.files)}\n"
                f"• *Status:* CANCELLED\n\n"
                f"✨ You can send a new PDF document right now to start a fresh order!"
            )
            await whatsapp_service.send_text_message(to_phone=sender, text=reply)
            return

        # Subcase C2: Confirmation command (User confirms all files are sent)
        elif cmd in ["DONE", "FINISHED", "CONFIRM", "CONFIRM & PAY", "CONFIRM ORDER", "PAY", "ALL DONE", "ALL FILES SENT", "READY"]:
            if not order or not order.files:
                await whatsapp_service.send_text_message(
                    to_phone=sender,
                    text="❌ No documents uploaded yet. Please send your PDF document first!",
                )
                return

            # Advance state machine
            if order.status == OrderState.FILES_UPLOADING.value:
                await transition_order(session, order, OrderState.FILES_UPLOADED, actor_type="CUSTOMER")
            if order.status == OrderState.FILES_UPLOADED.value:
                await transition_order(session, order, OrderState.CONFIGURATION_PENDING, actor_type="CUSTOMER")
            if order.status == OrderState.CONFIGURATION_PENDING.value:
                await transition_order(session, order, OrderState.CONFIGURED, actor_type="CUSTOMER")
                await transition_order(session, order, OrderState.PRICE_CALCULATED, actor_type="BACKEND")
                await transition_order(session, order, OrderState.AWAITING_CONFIRMATION, actor_type="CUSTOMER")
            elif order.status == OrderState.PRICE_CALCULATED.value:
                await transition_order(session, order, OrderState.AWAITING_CONFIRMATION, actor_type="CUSTOMER")

            if order.status == OrderState.AWAITING_CONFIRMATION.value:
                await transition_order(session, order, OrderState.PAYMENT_PENDING, actor_type="CUSTOMER")

            # Set 5-minute payment expiration
            now = datetime.now(timezone.utc)
            order.expires_at = now + timedelta(minutes=settings.ORDER_PAYMENT_TIMEOUT_MINUTES)
            await calculate_order_pricing_snapshot(session, order)
            await session.commit()

            # Build single comprehensive bill receipt with pickup timing
            file_count = len(order.files)
            files_breakdown = "\n".join([f"  {idx}. {f.original_filename} ({f.page_count} pages)" for idx, f in enumerate(order.files, 1)])
            base_charge = file_count * settings.PRICE_FILE_BASE_CHARGE
            print_charge = max(0.0, order.total_amount - base_charge)
            expiry_str = (now + timedelta(minutes=settings.ORDER_PAYMENT_TIMEOUT_MINUTES)).strftime("%I:%M %p")
            timing = get_order_pickup_timing(order, settings.PRINTER_SPEED_PPM)

            reply = (
                f"🧾 *Order Confirmed & Bill Receipt* (Order #{order.order_code})\n\n"
                f"• *Pickup Token:* *{order.pickup_token}*\n"
                f"• *Files Confirmed ({file_count} total):*\n{files_breakdown}\n\n"
                f"• *Total Pages:* {order.total_pages} pages\n"
                f"• *Document Handling:* ₹{base_charge:.2f}\n"
                f"• *Print Charge:* ₹{print_charge:.2f}\n"
                f"• *Total Amount:* *₹{order.total_amount:.2f}*\n\n"
                f"🕒 *Estimated Pickup Time:* ~{timing['eta_mins']} mins after payment (Ready by approx. {timing['ready_time_str']})\n"
                f"⏳ *Payment Window: {settings.ORDER_PAYMENT_TIMEOUT_MINUTES} MINUTES* (Expires at {expiry_str})\n"
                f"⚠️ *Important:* If payment is not completed within {settings.ORDER_PAYMENT_TIMEOUT_MINUTES} minutes, this order and its uploaded files will be permanently deleted!\n\n"
                f"💳 Please scan the QR code below or pay via UPI.\n"
                f"⚡ After paying, reply *PAID <12-digit UTR>* (e.g. `PAID 429512345678`) to instantly start printing!\n"
                f"ℹ️ (Find the 12-digit UPI Ref/UTR on your Google Pay, PhonePe, or Paytm receipt)\n"
                f"❌ Reply *CANCEL* to cancel this order anytime."
            )
            await whatsapp_service.send_text_message(to_phone=sender, text=reply)

            # Dispatch dynamic payment QR code in single flow with receipt
            await send_payment_qr(sender, order, session=session)
            return

        # Subcase C3: Payment Reverification command (supports optional 10-16 digit UTR or bare UTR)
        elif any(cmd.startswith(prefix) for prefix in ["PAID", "VERIFY", "REVERIFY", "UTR", "CHECK PAYMENT", "PAYMENT DONE", "PAYMENT COMPLETE"]) or (
            order and order.status in [OrderState.PAYMENT_PENDING.value, OrderState.PRICE_CALCULATED.value, OrderState.EXPIRED.value] and re.match(r"^\s*\d{10,16}\s*$", user_text.strip())
        ):
            if not order:
                # Check for most recent order in PAYMENT_PENDING or EXPIRED state for this user
                query = (
                    select(Order)
                    .where(Order.user_id == user.id, Order.status.in_([OrderState.PAYMENT_PENDING.value, OrderState.EXPIRED.value]))
                    .options(
                        selectinload(Order.files).selectinload(OrderFile.configuration),
                        selectinload(Order.user),
                    )
                    .order_by(desc(Order.created_at))
                )
                res = await session.execute(query)
                order = res.scalars().first()

            if not order:
                await whatsapp_service.send_text_message(
                    to_phone=sender,
                    text="ℹ️ No active order found to verify payment for. Send a PDF document to start a new order!",
                )
                return

            utr_match = re.search(r"\b(\d{10,16})\b", user_text)
            extracted_utr = utr_match.group(1) if utr_match else None

            result = await reverify_order_payment(session, order, utr=extracted_utr)
            if result.get("verified"):
                timing = get_order_pickup_timing(order, settings.PRINTER_SPEED_PPM)
                ref_info = f"\n• *Payment Ref / UTR:* `{result.get('utr')}`" if result.get('utr') else ""
                reply = (
                    f"✅ *Payment Confirmed!* (Order #{order.order_code})\n\n"
                    f"• *Status:* QUEUED FOR PRINTING\n"
                    f"• *Amount Paid:* ₹{order.total_amount:.2f}\n"
                    f"• *Pickup Token:* *{order.pickup_token}*{ref_info}\n"
                    f"🕒 *Estimated Pickup Time:* Ready in ~{timing['eta_mins']} mins (by approx. {timing['ready_time_str']})!\n\n"
                    f"🖨️ Your document has been sent to the print queue! Show Token *{order.pickup_token}* at the counter to collect your prints.\n\n"
                    f"✨ *Need more prints?* You can send another PDF right now to place a new order instantaneously!"
                )
            else:
                reply = result.get("message") or (
                    f"⏳ *Payment Verification Update* (Order #{order.order_code})\n\n"
                    f"• *Status:* {order.status}\n"
                    f"• *Payable Amount:* ₹{order.total_amount:.2f}\n"
                    f"• *Pickup Token:* *{order.pickup_token}*\n\n"
                    f"Payment not confirmed yet.\n\n"
                    f"👉 If you paid via UPI, reply with your 12-digit UTR/Ref number:\n"
                    f"*PAID <12-digit UTR>* (e.g. `PAID 429512345678`)\n"
                    f"👉 If paying in cash, show Token *{order.pickup_token}* at the counter.\n"
                    f"👉 Reply *CANCEL* to cancel this order anytime."
                )
            await whatsapp_service.send_text_message(to_phone=sender, text=reply)
            return

        # Subcase C4: Order status check
        elif cmd in ["STATUS", "TOKEN", "MY ORDER", "MY ORDERS", "ORDERS"]:
            active_orders = await get_all_active_user_orders(session, user)
            if not active_orders:
                await whatsapp_service.send_text_message(
                    to_phone=sender,
                    text="ℹ️ You have no active print orders. Send a PDF document to start an order anytime!",
                )
                return

            lines = ["📋 *Your Active Print Orders:*\n"]
            for idx, o in enumerate(active_orders, 1):
                timing = get_order_pickup_timing(o, settings.PRINTER_SPEED_PPM)
                lines.append(
                    f"*{idx}. Order #{o.order_code}*\n"
                    f"• *Pickup Token:* *{o.pickup_token}*\n"
                    f"• *Status:* {o.status}\n"
                    f"• *Files:* {len(o.files)} ({o.total_pages} pages)\n"
                    f"• *Amount:* ₹{o.total_amount:.2f}\n"
                    f"• *Estimated Pickup Time:* ~{timing['eta_mins']} mins (Ready by ~{timing['ready_time_str']})\n"
                )
            lines.extend([
                "👉 Reply *PAID* or *PAID <UTR>* to verify online payments.",
                "👉 Reply *CANCEL* to cancel an active order.",
                "👉 Send a new PDF to place another order instantaneously!"
            ])
            await whatsapp_service.send_text_message(to_phone=sender, text="\n".join(lines))
            return

        # Subcase C5: System limits & guidelines
        elif cmd in ["LIMIT", "LIMITS", "RULES", "HELP", "REORDER", "NEW ORDER", "NEW"]:
            reply = (
                f"ℹ️ *PrintShop System Limits & Quick Guide:*\n\n"
                f"• *Files per Order:* Up to {settings.MAX_FILES_PER_ORDER} PDF files in a single order\n"
                f"• *File Size Limit:* Up to {settings.MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB per file\n"
                f"• *Single Receipt Flow:* Upload all your files quietly, then reply *DONE* to receive your final receipt and payment QR in a single step\n"
                f"• *Payment Window:* {settings.ORDER_PAYMENT_TIMEOUT_MINUTES} minutes (unpaid orders and uploaded files are automatically deleted after {settings.ORDER_PAYMENT_TIMEOUT_MINUTES} minutes)\n"
                f"• *Multiple Orders:* Unlimited! You can run multiple orders simultaneously\n"
                f"• *Instant Reordering:* Send a new PDF document immediately after confirming or paying for an order\n"
                f"• *Cancellation:* Reply *CANCEL* anytime before printing\n\n"
                f"📄 Send a PDF document to start printing!"
            )
            await whatsapp_service.send_text_message(to_phone=sender, text=reply)
            return

        # Subcase C3: Printing Instructions (NLP Parsing)
        else:
            if not order or not order.files:
                reply = (
                    f"👋 *Welcome to PrintShop!*\n\n"
                    f"Please send the PDF document(s) you would like to print, and then specify your print options."
                )
                await whatsapp_service.send_text_message(to_phone=sender, text=reply)
                return

            # Invoke LLM NLP parser
            result = await interpret_print_instructions(
                session=session,
                order_id=order.id,
                user_text=user_text,
                files=order.files,
            )

            if result.ambiguity:
                q = result.clarification_question or "Could you please clarify your print options?"
                await whatsapp_service.send_text_message(to_phone=sender, text=f"❓ {q}")
                return

            # Apply parsed configuration to files
            if result.file_rules:
                for f_rule in result.file_rules:
                    matched_file = next(
                        (f for f in order.files if f.upload_sequence == f_rule.file_reference),
                        order.files[0] if len(order.files) == 1 else None,
                    )
                    if matched_file and matched_file.configuration:
                        cfg = matched_file.configuration
                        if f_rule.color_mode:
                            cfg.color_mode = f_rule.color_mode
                        if f_rule.default_sides:
                            cfg.default_sides = f_rule.default_sides
                        if f_rule.rules:
                            cfg.rules_json = json.dumps([r.model_dump() for r in f_rule.rules])

                # Advance state
                if order.status == OrderState.FILES_UPLOADING.value:
                    await transition_order(session, order, OrderState.FILES_UPLOADED, actor_type="CUSTOMER")
                if order.status == OrderState.FILES_UPLOADED.value:
                    await transition_order(session, order, OrderState.CONFIGURATION_PENDING, actor_type="CUSTOMER")
                if order.status == OrderState.CONFIGURATION_PENDING.value:
                    await transition_order(session, order, OrderState.CONFIGURED, actor_type="CUSTOMER")
                    await transition_order(session, order, OrderState.PRICE_CALCULATED, actor_type="BACKEND")
                    await transition_order(session, order, OrderState.AWAITING_CONFIRMATION, actor_type="BACKEND")

                # Recalculate price
                await calculate_order_pricing_snapshot(session, order)
                await session.commit()

                # Fetch updated configuration
                cfg_first = order.files[0].configuration if order.files else None
                color_desc = cfg_first.color_mode.upper() if cfg_first else "BW"
                sides_desc = cfg_first.default_sides.capitalize() if cfg_first else "Single"

                base_charge = len(order.files) * settings.PRICE_FILE_BASE_CHARGE
                print_charge = max(0.0, order.total_amount - base_charge)

                reply = (
                    f"✅ *Print Options Configured:*\n"
                    f"• *Sides:* {sides_desc}\n"
                    f"• *Color Mode:* {color_desc}\n"
                    f"• *Total Pages:* {order.total_pages}\n"
                    f"• *Pickup Token:* *{order.pickup_token}*\n\n"
                    f"💰 *Updated Cost Estimation:*\n"
                    f"• Document Handling: ₹{base_charge:.2f}\n"
                    f"• Printing ({order.total_pages} pages): ₹{print_charge:.2f}\n"
                    f"• *Total Cost: ₹{order.total_amount:.2f}*\n\n"
                    f"👉 Send another PDF to add more files (up to {settings.MAX_FILES_PER_ORDER})\n"
                    f"👉 Reply *DONE* (or *CONFIRM*) once you have sent all your files to get your payment QR!\n"
                    f"👉 Or reply *CANCEL* to cancel this order anytime"
                )
                await whatsapp_service.send_text_message(to_phone=sender, text=reply)
                return
