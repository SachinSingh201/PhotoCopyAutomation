import json
from typing import Any, Dict, Optional
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.core.logging import logger
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.models.user import ConversationSession, User
from backend.services.files.service import process_and_save_order_file
from backend.services.llm.parser import interpret_print_instructions
from backend.services.orders.service import create_order, get_or_create_user, get_order_by_id
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.pricing.engine import calculate_order_pricing_snapshot
from backend.services.whatsapp.service import whatsapp_service


async def get_active_customer_order(session: AsyncSession, user: User) -> Optional[Order]:
    """Retrieves the customer's current open/uncompleted print order, if any."""
    uncompleted_states = [
        OrderState.CREATED.value,
        OrderState.FILES_UPLOADING.value,
        OrderState.FILES_UPLOADED.value,
        OrderState.CONFIGURATION_PENDING.value,
        OrderState.CONFIGURED.value,
        OrderState.PRICE_CALCULATED.value,
        OrderState.AWAITING_CONFIRMATION.value,
        OrderState.PAYMENT_PENDING.value,
    ]
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

        # Create new order if customer has no active uncompleted order
        if not order:
            created_order = await create_order(session=session, user=user)
            order = await get_order_by_id(session, created_order.id)

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
        await session.commit()

        # Reload order to reflect fresh state
        order = await get_order_by_id(session, order.id)

        reply = (
            f"📄 *Document Received!*\n"
            f"• *File:* {order_file.original_filename} ({order_file.page_count} pages)\n"
            f"• *Total Files:* {len(order.files)} ({order.total_pages} total pages)\n"
            f"• *Order Code:* #{order.order_code}\n\n"
            f"💬 *Reply with your printing instructions:*\n"
            f"• e.g. *\"Single side all pages\"*\n"
            f"• e.g. *\"Black and white double sided\"*\n"
            f"• e.g. *\"Color first page, rest B&W\"*\n\n"
            f"Or reply *CONFIRM* to print with standard single-sided B&W!"
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

        # Subcase C1: Confirmation command
        if cmd in ["CONFIRM", "CONFIRM & PAY", "CONFIRM ORDER", "PAY"]:
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
                f"• *Files:* {len(order.files)}\n"
                f"• *Total Pages:* {order.total_pages}\n"
                f"• *Total Amount:* ₹{order.total_amount:.2f}\n"
                f"• *Pickup Token:* *{order.pickup_token}*\n\n"
                f"💳 *Payment Pending:* Please pay ₹{order.total_amount:.2f} at the counter using your pickup token *{order.pickup_token}*.\n"
                f"Once paid, your order will automatically be dispatched to the printer!"
            )
            await whatsapp_service.send_text_message(to_phone=sender, text=reply)
            return

        # Subcase C2: Order status check
        elif cmd in ["STATUS", "TOKEN", "MY ORDER"]:
            if not order:
                await whatsapp_service.send_text_message(
                    to_phone=sender,
                    text="ℹ️ You have no active print orders. Send a PDF document to start!",
                )
                return

            reply = (
                f"📋 *Order Status* (Order #{order.order_code})\n"
                f"• *Status:* {order.status}\n"
                f"• *Pickup Token:* *{order.pickup_token}*\n"
                f"• *Pages:* {order.total_pages}\n"
                f"• *Total Amount:* ₹{order.total_amount:.2f}\n"
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

                reply = (
                    f"✅ *Print Options Configured:*\n"
                    f"• *Sides:* {sides_desc}\n"
                    f"• *Color Mode:* {color_desc}\n"
                    f"• *Total Pages:* {order.total_pages}\n"
                    f"• *Estimated Total:* ₹{order.total_amount:.2f}\n\n"
                    f"👉 Reply *CONFIRM* to lock in your order and get your pickup token!"
                )
                await whatsapp_service.send_text_message(to_phone=sender, text=reply)
