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

    # Mock download_media to return our valid 6-page test PDF
    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}

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

        # Verify WhatsApp confirmation was sent
        assert mock_send.call_count == 1
        sent_text = mock_send.call_args[1]["text"]
        assert "Document Received!" in sent_text
        assert "my_notes.pdf" in sent_text
        assert "6 pages" in sent_text

        # 2. Customer replies with instructions: "Single side all pages"
        mock_send.reset_mock()
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

        # 3. Customer confirms order: "CONFIRM"
        mock_send.reset_mock()
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
