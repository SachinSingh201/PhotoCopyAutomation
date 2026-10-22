import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.core.config import settings
from backend.models.order import Order
from backend.models.user import User
from backend.services.orders.state_machine import OrderState
from backend.services.whatsapp.bot import handle_inbound_whatsapp_event, get_active_customer_order


@pytest.mark.asyncio
async def test_bot_document_upload_and_instruction_flow(db_session, valid_pdf_6_pages):
    phone = "919988776655"

    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_document", new_callable=AsyncMock) as mock_doc:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}
        mock_doc.return_value = {"status": "success"}

        # 1. Customer sends document PDF
        doc_event = {
            "type": "document",
            "sender": phone,
            "display_name": "Test Customer",
            "document": {
                "id": "msg_doc_1",
                "filename": "my_notes.pdf",
                "mime_type": "application/pdf",
            },
        }

        await handle_inbound_whatsapp_event(db_session, doc_event)

        # Verify order was created with the file
        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        order = await get_active_customer_order(db_session, user)
        assert order is not None
        assert order.status == OrderState.FILES_UPLOADED.value
        assert len(order.files) == 1
        assert order.files[0].page_count == 6
        assert order.total_pages == 6

        # Verify WhatsApp confirmation text was sent with page count
        assert mock_send.call_count == 1
        sent_text = mock_send.call_args[1]["text"]
        assert "File 1 of 10 Received!" in sent_text
        assert "my_notes.pdf" in sent_text
        assert "6 pages" in sent_text
        assert "DONE" in sent_text

        # QR code image should NOT be dispatched on initial file upload
        assert mock_img.call_count == 0

        # 2. Customer replies with instructions: "Single side all pages"
        mock_send.reset_mock()
        mock_img.reset_mock()
        text_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Test Customer",
            "text": "Single side all pages",
        }
        await handle_inbound_whatsapp_event(db_session, text_event)

        # Refresh order
        order = await get_active_customer_order(db_session, user)
        assert order.status == OrderState.AWAITING_CONFIRMATION.value
        assert mock_send.call_count == 1
        instruction_reply = mock_send.call_args[1]["text"]
        assert "Print Options Configured" in instruction_reply
        assert "Single" in instruction_reply
        assert "Updated Cost Estimation" in instruction_reply
        # QR code should NOT be sent on instructions either
        assert mock_img.call_count == 0

        # 3. Customer confirms all files are sent: "CONFIRM"
        mock_send.reset_mock()
        mock_img.reset_mock()
        confirm_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Test Customer",
            "text": "CONFIRM",
        }
        await handle_inbound_whatsapp_event(db_session, confirm_event)

        # Verify state advanced to PAYMENT_PENDING with 5-minute timeout
        order = await get_active_customer_order(db_session, user)
        assert order.status == OrderState.PAYMENT_PENDING.value
        assert order.expires_at is not None
        assert mock_send.call_count == 1
        confirm_reply = mock_send.call_args[1]["text"]
        assert "Order Confirmed & Bill Receipt" in confirm_reply
        assert f"Payment Window: {settings.ORDER_PAYMENT_TIMEOUT_MINUTES} MINUTES" in confirm_reply
        assert order.pickup_token in confirm_reply
        # QR code image is now dispatched in this single confirmation step
        assert mock_img.call_count == 1

        # 4. Customer asks to verify payment: "VERIFY"
        mock_send.reset_mock()
        mock_img.reset_mock()
        verify_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Test Customer",
            "text": "VERIFY",
        }
        await handle_inbound_whatsapp_event(db_session, verify_event)
        assert mock_send.call_count == 1
        verify_reply = mock_send.call_args[1]["text"]
        assert "Payment Verification" in verify_reply
        assert "12-digit UTR" in verify_reply

        # 5. Customer replies with bare 12-digit UTR: "429512345678"
        saved_order_code = order.order_code
        mock_send.reset_mock()
        mock_img.reset_mock()
        utr_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Test Customer",
            "text": "429512345678",
        }
        await handle_inbound_whatsapp_event(db_session, utr_event)
        assert mock_send.call_count == 1
        paid_reply = mock_send.call_args[1]["text"]
        assert "Payment Confirmed!" in paid_reply
        assert "429512345678" in paid_reply
        assert order.pickup_token in paid_reply

        # Verify order is queued
        refreshed = (await db_session.execute(select(Order).where(Order.order_code == saved_order_code))).scalar_one()
        assert refreshed.status in [OrderState.PAID.value, OrderState.QUEUED.value]


@pytest.mark.asyncio
async def test_bot_cancel_order_flow(db_session, valid_pdf_6_pages):
    phone = "919988771122"

    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}

        # 1. Customer uploads document
        doc_event = {
            "type": "document",
            "sender": phone,
            "display_name": "Cancelling Customer",
            "document": {
                "id": "doc_cancel_1",
                "filename": "draft_doc.pdf",
                "mime_type": "application/pdf",
            },
        }
        await handle_inbound_whatsapp_event(db_session, doc_event)

        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        order = await get_active_customer_order(db_session, user)
        assert order is not None
        assert order.status == OrderState.FILES_UPLOADED.value

        # 2. Customer sends CANCEL
        mock_send.reset_mock()
        cancel_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Cancelling Customer",
            "text": "CANCEL",
        }
        await handle_inbound_whatsapp_event(db_session, cancel_event)

        # Refresh order from DB
        refreshed_order = (await db_session.execute(select(Order).where(Order.id == order.id))).scalar_one()
        assert refreshed_order.status == OrderState.CANCELLED.value

        # Active customer order should now be None
        active_now = await get_active_customer_order(db_session, user)
        assert active_now is None

        # Verify WhatsApp message sent
        assert mock_send.call_count == 1
        cancel_reply = mock_send.call_args[1]["text"]
        assert "has been cancelled" in cancel_reply
        assert "start a fresh order" in cancel_reply


@pytest.mark.asyncio
async def test_bot_multiple_files_in_draft_and_instant_reorder(db_session, valid_pdf_6_pages):
    phone = "919988773344"

    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}

        # 1. Upload first file (6 pages)
        doc_1 = {
            "type": "document",
            "sender": phone,
            "display_name": "Multi Customer",
            "document": {"id": "doc_m_1", "filename": "chapter1.pdf", "mime_type": "application/pdf"},
        }
        await handle_inbound_whatsapp_event(db_session, doc_1)

        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        order1 = await get_active_customer_order(db_session, user)
        assert len(order1.files) == 1
        assert order1.total_pages == 6

        # 2. Upload second file to same draft (6 pages -> 12 total pages)
        doc_2 = {
            "type": "document",
            "sender": phone,
            "display_name": "Multi Customer",
            "document": {"id": "doc_m_2", "filename": "chapter2.pdf", "mime_type": "application/pdf"},
        }
        await handle_inbound_whatsapp_event(db_session, doc_2)

        # Refresh order1
        order1 = (await db_session.execute(
            select(Order)
            .where(Order.id == order1.id)
            .options(selectinload(Order.files))
        )).scalar_one()
        assert len(order1.files) == 2
        assert order1.total_pages == 12

        # 3. Customer confirms order 1 (moves to PAYMENT_PENDING)
        confirm_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Multi Customer",
            "text": "CONFIRM",
        }
        await handle_inbound_whatsapp_event(db_session, confirm_event)
        order1 = (await db_session.execute(select(Order).where(Order.id == order1.id))).scalar_one()
        assert order1.status == OrderState.PAYMENT_PENDING.value

        # 4. Instant re-order: customer immediately sends another PDF for a NEW order
        doc_3 = {
            "type": "document",
            "sender": phone,
            "display_name": "Multi Customer",
            "document": {"id": "doc_m_3", "filename": "new_project.pdf", "mime_type": "application/pdf"},
        }
        await handle_inbound_whatsapp_event(db_session, doc_3)

        # There should now be two distinct orders for this user
        all_user_orders = (await db_session.execute(
            select(Order).where(Order.user_id == user.id).order_by(Order.created_at.asc())
        )).scalars().all()
        assert len(all_user_orders) == 2
        order2 = all_user_orders[1]
        assert order2.id != order1.id
        assert order2.order_code != order1.order_code
        assert order2.pickup_token != order1.pickup_token
        assert order2.status == OrderState.FILES_UPLOADED.value
        assert len(order2.files) == 1

        # 5. Customer checks STATUS command
        mock_send.reset_mock()
        status_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Multi Customer",
            "text": "STATUS",
        }
        await handle_inbound_whatsapp_event(db_session, status_event)
        status_reply = mock_send.call_args[1]["text"]
        assert "Active Print Orders" in status_reply
        assert order1.order_code in status_reply
        assert order2.order_code in status_reply

        # 6. Customer checks LIMITS command
        mock_send.reset_mock()
        limits_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Multi Customer",
            "text": "LIMITS",
        }
        await handle_inbound_whatsapp_event(db_session, limits_event)
        limits_reply = mock_send.call_args[1]["text"]
        assert "System Limits & Quick Guide" in limits_reply
        assert "10 PDF files" in limits_reply
        assert f"{settings.ORDER_PAYMENT_TIMEOUT_MINUTES} minutes" in limits_reply


@pytest.mark.asyncio
async def test_bot_payment_timeout_unpaid_order_expiration(db_session, valid_pdf_6_pages):
    """Verifies that unpaid orders exceeding the payment timeout are expired and deleted."""
    from datetime import datetime, timedelta, timezone
    from backend.services.orders.service import cleanup_expired_orders

    phone = "919988775566"
    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}

        # 1. Upload file
        await handle_inbound_whatsapp_event(db_session, {
            "type": "document",
            "sender": phone,
            "display_name": "Expiring Customer",
            "document": {"id": "doc_exp_1", "filename": "expiring.pdf", "mime_type": "application/pdf"},
        })

        # 2. Confirm order (moves to PAYMENT_PENDING with 5 min timeout)
        await handle_inbound_whatsapp_event(db_session, {
            "type": "text",
            "sender": phone,
            "display_name": "Expiring Customer",
            "text": "DONE",
        })

        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        order = await get_active_customer_order(db_session, user)
        assert order is not None
        assert order.status == OrderState.PAYMENT_PENDING.value

        # 3. Simulate passage exceeding payment timeout
        past_time = datetime.now(timezone.utc) - timedelta(minutes=settings.ORDER_PAYMENT_TIMEOUT_MINUTES + 1)
        order.expires_at = past_time
        await db_session.commit()

        # 4. Trigger cleanup
        mock_send.reset_mock()
        cleaned = await cleanup_expired_orders(db_session)
        assert order.order_code in cleaned

        # Verify order transitioned to EXPIRED
        refreshed = (await db_session.execute(select(Order).where(Order.id == order.id))).scalar_one()
        assert refreshed.status == OrderState.EXPIRED.value

        # Verify WhatsApp expiration notification was sent
        assert mock_send.call_count == 1
        exp_text = mock_send.call_args[1]["text"]
        assert "Expired & Deleted" in exp_text
        assert f"{settings.ORDER_PAYMENT_TIMEOUT_MINUTES}-minute payment window" in exp_text


@pytest.mark.asyncio
async def test_youpi_webhook_payment_verification(db_session, valid_pdf_6_pages):
    """Verifies that YouPI-PG webhook marks order as PAID and enqueues print job."""
    from backend.services.payments.youpi import process_youpi_payment_success

    phone = "919988778899"
    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}

        # Upload and confirm order
        await handle_inbound_whatsapp_event(db_session, {
            "type": "document",
            "sender": phone,
            "display_name": "YouPI Customer",
            "document": {"id": "doc_yp_1", "filename": "youpi_doc.pdf", "mime_type": "application/pdf"},
        })
        await handle_inbound_whatsapp_event(db_session, {
            "type": "text",
            "sender": phone,
            "display_name": "YouPI Customer",
            "text": "DONE",
        })

        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        order = await get_active_customer_order(db_session, user)
        assert order.status == OrderState.PAYMENT_PENDING.value

        # Simulate YouPI-PG Webhook call
        mock_send.reset_mock()
        webhook_payload = {
            "event": "payment.success",
            "order_id": order.order_code,
            "amount": order.total_amount,
            "utr": "UTR998877665544",
            "status": "SUCCESS",
        }
        success, message, updated_order = await process_youpi_payment_success(db_session, webhook_payload)
        assert success is True
        assert updated_order.status == OrderState.QUEUED.value

        # Verify customer received payment verified notification with pickup token
        assert mock_send.call_count == 1
        paid_text = mock_send.call_args[1]["text"]
        assert "Payment Verified via UPI!" in paid_text
        assert order.pickup_token in paid_text
        assert "UTR998877665544" in paid_text


@pytest.mark.asyncio
async def test_youpi_webhook_expired_event(db_session, valid_pdf_6_pages):
    """Verifies that an EXPIRED webhook event from YouPI-PG immediately marks order expired and deletes files."""
    from unittest.mock import AsyncMock, patch
    from backend.core.config import settings
    from backend.services.payments.youpi import process_youpi_payment_success

    phone = "919900112233"
    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}

        # Upload and confirm order
        await handle_inbound_whatsapp_event(db_session, {
            "type": "document",
            "sender": phone,
            "display_name": "Timeout Customer",
            "document": {"id": "doc_to_1", "filename": "timeout.pdf", "mime_type": "application/pdf"},
        })
        await handle_inbound_whatsapp_event(db_session, {
            "type": "text",
            "sender": phone,
            "display_name": "Timeout Customer",
            "text": "DONE",
        })

        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        order = await get_active_customer_order(db_session, user)
        assert order.status == OrderState.PAYMENT_PENDING.value

        # Create dummy file on disk in order directory
        order_dir = settings.storage_path / "orders" / order.id
        order_dir.mkdir(parents=True, exist_ok=True)
        dummy_file = order_dir / "timeout.pdf"
        dummy_file.write_bytes(b"%PDF dummy content")
        assert dummy_file.exists()

        # Simulate YouPI-PG Webhook with event: EXPIRED
        mock_send.reset_mock()
        webhook_payload = {
            "event": "EXPIRED",
            "orderId": order.order_code,
            "paymentId": "yp_session_expired_1",
            "status": "EXPIRED",
        }
        success, message, updated_order = await process_youpi_payment_success(db_session, webhook_payload)
        assert success is True
        assert updated_order.status == OrderState.EXPIRED.value
        assert not dummy_file.exists()

        # Verify WhatsApp cancellation notice sent to customer
        assert mock_send.call_count == 1
        msg = mock_send.call_args[1]["text"]
        assert "Expired & Deleted" in msg
        assert order.pickup_token in msg

