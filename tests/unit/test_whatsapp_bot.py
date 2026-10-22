import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.orm import selectinload

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

        # Verify WhatsApp confirmation text was sent with page count and cost estimation
        assert mock_send.call_count == 1
        sent_text = mock_send.call_args[1]["text"]
        assert "Document Received & Analyzed!" in sent_text
        assert "my_notes.pdf" in sent_text
        assert "6 pages" in sent_text
        assert "Cost Estimation" in sent_text
        assert "Estimated Total" in sent_text
        assert order.pickup_token in sent_text

        # Verify QR code image was dispatched
        assert mock_img.call_count == 1
        img_caption = mock_img.call_args[1]["caption"]
        assert "Scan QR to Pay" in img_caption
        assert order.pickup_token in img_caption

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
        assert mock_img.call_count == 1

        # 3. Customer confirms order: "CONFIRM"
        mock_send.reset_mock()
        mock_img.reset_mock()
        confirm_event = {
            "type": "text",
            "sender": phone,
            "display_name": "Test Customer",
            "text": "CONFIRM",
        }
        await handle_inbound_whatsapp_event(db_session, confirm_event)

        # Verify state advanced to PAYMENT_PENDING
        order = await get_active_customer_order(db_session, user)
        assert order.status == OrderState.PAYMENT_PENDING.value
        assert mock_send.call_count == 1
        confirm_reply = mock_send.call_args[1]["text"]
        assert "Order Confirmed!" in confirm_reply
        assert order.pickup_token in confirm_reply
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
        assert "Payment Verification Update" in verify_reply or "Payment Confirmed!" in verify_reply


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
        assert "Instant Reordering" in limits_reply
