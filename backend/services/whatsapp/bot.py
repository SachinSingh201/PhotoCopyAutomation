import json
import urllib.parse
from typing import Any, Dict, List, Optional
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.core.config import settings
from backend.core.logging import logger
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.models.user import ConversationSession, User
from backend.services.files.service import process_and_save_order_file
from backend.services.llm.parser import interpret_print_instructions
from backend.services.orders.service import create_order, get_or_create_user, get_order_by_id
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.payments.razorpay import create_razorpay_payment_link, reverify_order_payment
from backend.services.payments.upi import generate_qr_code_url, generate_upi_uri
from backend.services.pricing.engine import calculate_order_pricing_snapshot
from backend.services.whatsapp.service import whatsapp_service


async def send_payment_qr(sender: str, order: Order, session: Optional[AsyncSession] = None) -> None:
    """
    Dispatches the shop counter QR code (or local dynamic UPI QR) with the exact payable amount,
    accompanied by a Razorpay online payment link and verification instructions.
    """
    rzp_url = None
    try:
        rzp_info = await create_razorpay_payment_link(order)
        if rzp_info:
            rzp_url = rzp_info.get("short_url")
    except Exception as e:
        logger.warning(f"Could not generate Razorpay payment link: {e}")

    # Build clear caption detailing payable amount and tokens
    caption_lines = [
        f"💳 *Scan QR to Pay: ₹{order.total_amount:.2f}*",
        f"• *Shop:* {settings.SHOP_NAME}",
        f"• *UPI ID:* `{settings.SHOP_UPI_ID}`",
        f"• *Order Code:* #{order.order_code}",
        f"• *Pickup Token:* *{order.pickup_token}*",
        f"• *Total Payable:* *₹{order.total_amount:.2f}*",
    ]
    if rzp_url:
        caption_lines.extend([
            "",
            f"🌐 *Or Pay via Razorpay (UPI / Cards / NetBanking):*",
            f"{rzp_url}",
        ])
    caption_lines.extend([
        "",
        "⚡ *After paying:* Reply *PAID* or *VERIFY* to auto-check payment and release your print job!",
    ])
    caption = "\n".join(caption_lines)

    # Determine image path: custom shop counter QR, or locally generated dynamic QR
    qr_image_path = None
    if settings.shop_qr_file.exists():
        qr_image_path = str(settings.shop_qr_file)
    else:
        try:
            import qrcode
            upi_uri = generate_upi_uri(
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
    )
    if rzp_url:
        fallback_text += f"\n🌐 *Pay Online via Razorpay:*\n{rzp_url}\n"
    fallback_text += "\n👉 Show token at counter or reply *PAID* / *VERIFY* once completed!"
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

    display_name = event.get("display_name") or "Customer"
    event_type = event.get("type", "text")

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
                    f"👉 Reply *CONFIRM* to lock in your order with these files.\n"
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

        base_charge = len(order.files) * settings.PRICE_FILE_BASE_CHARGE
        print_charge = max(0.0, order.total_amount - base_charge)

        reply = (
            f"📄 *Document Received & Analyzed!* (File {len(order.files)} of {settings.MAX_FILES_PER_ORDER})\n"
            f"• *File:* {order_file.original_filename} ({order_file.page_count} pages)\n"
            f"• *Total in this Order:* {len(order.files)} files ({order.total_pages} pages total)\n"
            f"• *Order Code:* #{order.order_code}\n"
            f"• *Pickup Token:* *{order.pickup_token}*\n\n"
            f"💰 *Cost Estimation (Standard Single-Sided B&W):*\n"
            f"• Document Handling: ₹{base_charge:.2f}\n"
            f"• Print Charge ({order.total_pages} pages @ ₹{settings.PRICE_BW_SINGLE:.2f}/page): ₹{print_charge:.2f}\n"
            f"• *Estimated Total: ₹{order.total_amount:.2f}*\n\n"
            f"💬 *What to do next:*\n"
            f"• Send another PDF to add more files to this order (up to {settings.MAX_FILES_PER_ORDER})\n"
            f"• Reply with custom print instructions: e.g. *\"Double sided\"*, *\"Color front page\"*\n"
            f"• Reply *CONFIRM* to lock in your order with this price\n"
            f"• Or reply *CANCEL* to cancel this order anytime"
        )
        await whatsapp_service.send_text_message(to_phone=sender, text=reply)

        # Dispatch dynamic payment QR code with exact cost
        await send_payment_qr(sender, order, session=session)
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

        # Subcase C2: Confirmation command
        elif cmd in ["CONFIRM", "CONFIRM & PAY", "CONFIRM ORDER", "PAY"]:
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

            await calculate_order_pricing_snapshot(session, order)
            await session.commit()

            reply = (
                f"🧾 *Order Confirmed!* (Order #{order.order_code})\n\n"
                f"• *Files:* {len(order.files)} files ({order.total_pages} total pages)\n"
                f"• *Total Amount:* ₹{order.total_amount:.2f}\n"
                f"• *Pickup Token:* *{order.pickup_token}*\n\n"
                f"💳 *Payment Pending:* Please pay ₹{order.total_amount:.2f} using the QR code above or via the Razorpay link.\n"
                f"⚡ *After paying:* Reply *PAID* or *VERIFY* to auto-release your prints!\n"
                f"❌ *Want to cancel?* Reply *CANCEL* anytime before printing.\n\n"
                f"✨ *Need another print job?* You can send another PDF right now to create a separate order instantaneously!"
            )
            await whatsapp_service.send_text_message(to_phone=sender, text=reply)

            # Dispatch dynamic payment QR code with exact cost
            await send_payment_qr(sender, order, session=session)
            return

        # Subcase C3: Payment Reverification command
        elif cmd in ["PAID", "VERIFY", "REVERIFY", "CHECK PAYMENT", "PAYMENT DONE", "PAYMENT COMPLETE", "STATUS PAYMENT"]:
            if not order:
                await whatsapp_service.send_text_message(
                    to_phone=sender,
                    text="ℹ️ No active order found to verify payment for. Send a PDF document to start!",
                )
                return

            result = await reverify_order_payment(session, order)
            if result.get("verified"):
                reply = (
                    f"✅ *Payment Confirmed!* (Order #{order.order_code})\n\n"
                    f"• *Status:* QUEUED FOR PRINTING\n"
                    f"• *Amount Paid:* ₹{order.total_amount:.2f}\n"
                    f"• *Pickup Token:* *{order.pickup_token}*\n\n"
                    f"🖨️ Your document has been sent to the print queue! Show Token *{order.pickup_token}* at the counter to collect your prints.\n\n"
                    f"✨ *Need more prints?* You can send another PDF right now to place a new order instantaneously!"
                )
            else:
                reply = (
                    f"⏳ *Payment Verification Update* (Order #{order.order_code})\n\n"
                    f"• *Status:* {order.status}\n"
                    f"• *Payable Amount:* ₹{order.total_amount:.2f}\n"
                    f"• *Pickup Token:* *{order.pickup_token}*\n\n"
                    f"{result.get('message', 'Payment not confirmed yet.')}\n\n"
                    f"👉 If paid at the counter, show Token *{order.pickup_token}* to the shop owner.\n"
                    f"👉 If paying online, complete via the Razorpay link and reply *VERIFY*.\n"
                    f"👉 Reply *CANCEL* if you wish to cancel this order."
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
                lines.append(
                    f"*{idx}. Order #{o.order_code}*\n"
                    f"• *Pickup Token:* *{o.pickup_token}*\n"
                    f"• *Status:* {o.status}\n"
                    f"• *Files:* {len(o.files)} ({o.total_pages} pages)\n"
                    f"• *Amount:* ₹{o.total_amount:.2f}\n"
                )
            lines.extend([
                "👉 Reply *PAID* to verify online payments.",
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
                f"• *Multiple Orders:* Unlimited! You can have multiple orders in progress simultaneously.\n"
                f"• *Instant Reordering:* Send a new PDF document immediately after confirming or paying for an order.\n"
                f"• *Cancellation:* Reply *CANCEL* anytime before printing.\n"
                f"• *Order Reminder:* Unpaid orders remain held for 20 minutes before cleanup.\n\n"
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
                    f"👉 Reply *CONFIRM* to lock in your order!\n"
                    f"👉 Send another PDF to add more files (up to {settings.MAX_FILES_PER_ORDER})\n"
                    f"👉 Or reply *CANCEL* to cancel this order anytime"
                )
                await whatsapp_service.send_text_message(to_phone=sender, text=reply)

                # Dispatch dynamic payment QR code with exact cost
                await send_payment_qr(sender, order, session=session)
