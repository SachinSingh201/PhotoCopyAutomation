import pytest
from httpx import ASGITransport, AsyncClient
from backend.main import app
from backend.services.payments.upi import (
    generate_qr_code_url,
    generate_upi_uri,
    validate_qr_payload,
    validate_upi_id,
)


def test_validate_upi_id():
    # Valid UPIS
    assert validate_upi_id("printshop@upi")["valid"] is True
    assert validate_upi_id("9876543210@paytm")["valid"] is True
    assert validate_upi_id("john.doe@okhdfcbank")["valid"] is True
    assert validate_upi_id("shop-express_01@icici")["valid"] is True

    # Invalid UPIs
    assert validate_upi_id("")["valid"] is False
    assert validate_upi_id("plainstring")["valid"] is False
    assert validate_upi_id("@handle")["valid"] is False
    assert validate_upi_id("user@")["valid"] is False


def test_generate_upi_uri_and_qr():
    uri = generate_upi_uri(
        upi_id="printshop@upi",
        payee_name="Print Express Hub",
        amount=25.50,
        order_code="ORD-1234",
        pickup_token="ABC-999",
    )
    assert uri.startswith("upi://pay?")
    assert "pa=printshop%40upi" in uri or "pa=printshop@upi" in uri
    assert "am=25.50" in uri
    assert "cu=INR" in uri
    assert "ABC-999" in uri

    qr_url = generate_qr_code_url(uri, size=350)
    assert "api.qrserver.com" in qr_url
    assert "size=350x350" in qr_url


def test_validate_qr_payload():
    # Valid UPI URI
    valid_uri = generate_upi_uri(
        upi_id="mybusiness@icici",
        payee_name="My Business",
        amount=100.0,
        order_code="ORD-5555",
        pickup_token="TOK-12",
    )
    result = validate_qr_payload(valid_uri)
    assert result["is_valid"] is True
    assert result["payee_vpa"] == "mybusiness@icici"
    assert result["amount"] == 100.0
    assert result["currency"] == "INR"
    assert len(result["errors"]) == 0

    # Invalid scheme
    res_bad_scheme = validate_qr_payload("http://google.com?pa=test@upi")
    assert res_bad_scheme["is_valid"] is False
    assert any("scheme" in e for e in res_bad_scheme["errors"])

    # Missing amount
    res_no_amt = validate_qr_payload("upi://pay?pa=test@upi&cu=INR")
    assert res_no_amt["is_valid"] is False
    assert any("am" in e for e in res_no_amt["errors"])


@pytest.mark.asyncio
async def test_admin_payment_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Admin-Secret": "6396"}

        # 1. Get payment settings
        get_res = await client.get("/api/v1/admin/payments/settings", headers=headers)
        assert get_res.status_code == 200
        data = get_res.json()
        assert "shop_upi_id" in data
        assert data["is_valid_upi"] is True

        # 2. Update payment settings
        put_res = await client.put(
            "/api/v1/admin/payments/settings",
            headers=headers,
            json={"shop_upi_id": "testshop@okhdfcbank", "shop_name": "Test Shop Express"},
        )
        assert put_res.status_code == 200
        assert put_res.json()["shop_upi_id"] == "testshop@okhdfcbank"

        # 3. Reject invalid UPI
        bad_put = await client.put(
            "/api/v1/admin/payments/settings",
            headers=headers,
            json={"shop_upi_id": "invalid_no_at_sign"},
        )
        assert bad_put.status_code == 400

        # 4. Validate QR endpoint
        qr_val = await client.get(
            "/api/v1/admin/payments/validate-qr?upi_id=testshop@okhdfcbank&amount=15.0&order_code=TESTORD&pickup_token=T-100",
            headers=headers,
        )
        assert qr_val.status_code == 200
        assert qr_val.json()["is_valid"] is True


@pytest.mark.asyncio
async def test_youpi_webhook_http_endpoint(client, db_session, valid_pdf_6_pages):
    import hashlib
    import hmac
    import json
    from unittest.mock import AsyncMock, patch
    from sqlalchemy import select
    from backend.core.config import settings
    from backend.models.user import User
    from backend.services.whatsapp.bot import handle_inbound_whatsapp_event, get_active_customer_order

    phone = "919911223344"
    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}

        await handle_inbound_whatsapp_event(db_session, {
            "type": "document",
            "sender": phone,
            "display_name": "HTTP YouPI Customer",
            "document": {"id": "doc_yp_http", "filename": "test.pdf", "mime_type": "application/pdf"},
        })
        await handle_inbound_whatsapp_event(db_session, {
            "type": "text",
            "sender": phone,
            "display_name": "HTTP YouPI Customer",
            "text": "DONE",
        })

        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        order = await get_active_customer_order(db_session, user)

        webhook_body = {
            "event": "payment.success",
            "order_id": order.order_code,
            "amount": order.total_amount,
            "utr": "UTR_HTTP_TEST_123",
            "status": "SUCCESS",
        }
        body_bytes = json.dumps(webhook_body).encode("utf-8")
        sig = hmac.new(
            (settings.YOUPI_WEBHOOK_SECRET or "").encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()

        res = await client.post(
            "/api/v1/payments/youpi/webhook",
            content=body_bytes,
            headers={"Content-Type": "application/json", "x-youpi-signature": sig},
        )
        assert res.status_code == 200, f"Expected 200 but got {res.status_code}: {res.text}"
        data = res.json()
        assert data["status"] == "success"
        assert data["order_code"] == order.order_code
        assert data["pickup_token"] == order.pickup_token


@pytest.mark.asyncio
async def test_paid_with_customer_utr_and_pickup_timing(db_session, valid_pdf_6_pages):
    from unittest.mock import AsyncMock, patch
    from sqlalchemy import select
    from backend.models.order import Order
    from backend.models.user import User
    from backend.services.orders.state_machine import OrderState
    from backend.services.whatsapp.bot import handle_inbound_whatsapp_event, get_active_customer_order

    phone = "919955443322"
    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}

        # 1. Customer uploads PDF
        await handle_inbound_whatsapp_event(db_session, {
            "type": "document",
            "sender": phone,
            "display_name": "UTR Customer",
            "document": {"id": "doc_utr_test", "filename": "sample.pdf", "mime_type": "application/pdf"},
        })

        # 2. Customer confirms with DONE
        await handle_inbound_whatsapp_event(db_session, {
            "type": "text",
            "sender": phone,
            "display_name": "UTR Customer",
            "text": "DONE",
        })

        # Verify bill receipt sent with pickup timing
        done_call_args = mock_send.call_args_list[-1]
        assert "Estimated Pickup Time" in done_call_args.kwargs.get("text", "") or "Estimated Pickup Time" in str(done_call_args)

        # 3. Customer pays on Google Pay and replies with 12-digit UTR: "PAID 429512345678"
        with patch("backend.services.payments.razorpay.check_youpi_observation_for_utr", return_value={"found": True, "amount_matches": True, "actual_amount": 6.0}):
            await handle_inbound_whatsapp_event(db_session, {
                "type": "text",
                "sender": phone,
                "display_name": "UTR Customer",
                "text": "PAID 429512345678",
            })

        # 4. Verify payment was verified and order transitioned to PAID / QUEUED
        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        paid_order_res = await db_session.execute(
            select(Order).where(Order.user_id == user.id)
        )
        paid_order = paid_order_res.scalars().first()
        assert paid_order.status in [OrderState.PAID.value, OrderState.QUEUED.value]

        # Verify confirmation message was dispatched to WhatsApp with pickup token & pickup time
        paid_reply = mock_send.call_args_list[-1]
        msg_text = paid_reply.kwargs.get("text", "") or str(paid_reply)
        assert "Payment Confirmed" in msg_text
        assert "429512345678" in msg_text
        assert paid_order.pickup_token in msg_text
        assert "Estimated Pickup Time" in msg_text


@pytest.mark.asyncio
async def test_fake_utr_rejected_in_strict_mode(db_session, valid_pdf_6_pages):
    """Verifies that fake or unrecorded UTRs are strictly rejected and not queued."""
    from unittest.mock import AsyncMock, patch
    from sqlalchemy import select
    from backend.models.order import Order
    from backend.models.user import User
    from backend.services.orders.state_machine import OrderState
    from backend.services.whatsapp.bot import handle_inbound_whatsapp_event

    phone = "919955448899"
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
            "display_name": "Strict Customer",
            "document": {"id": "doc_fake_test", "filename": "doc.pdf", "mime_type": "application/pdf"},
        })
        await handle_inbound_whatsapp_event(db_session, {
            "type": "text",
            "sender": phone,
            "display_name": "Strict Customer",
            "text": "DONE",
        })

        # Customer sends fake 12-digit UTR with NO matching bank observation
        await handle_inbound_whatsapp_event(db_session, {
            "type": "text",
            "sender": phone,
            "display_name": "Strict Customer",
            "text": "PAID 999988887777",
        })

        user = (await db_session.execute(select(User).where(User.phone_number == phone))).scalar_one()
        order = (await db_session.execute(select(Order).where(Order.user_id == user.id))).scalar_one()

        # Order must REMAIN in PAYMENT_PENDING - must NOT be marked PAID or QUEUED
        assert order.status == OrderState.PAYMENT_PENDING.value

        # Customer must be informed that bank credit was not found
        rejection_reply = mock_send.call_args_list[-1]
        msg = rejection_reply.kwargs.get("text", "") or str(rejection_reply)
        assert "Bank Credit Not Found" in msg
        assert "999988887777" in msg


@pytest.mark.asyncio
async def test_upload_rate_limiter_blocks_more_than_50_pdfs(db_session, valid_pdf_6_pages):
    """Verifies that sending >50 PDFs in less than 5 minutes triggers rate limiting and halts media downloads."""
    from unittest.mock import AsyncMock, patch
    from backend.services.security import upload_rate_limiter
    from backend.services.whatsapp.bot import handle_inbound_whatsapp_event

    phone = "919900112233"
    upload_rate_limiter.reset(phone)

    with patch("backend.services.whatsapp.bot.whatsapp_service.download_media", new_callable=AsyncMock) as mock_dl, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_text_message", new_callable=AsyncMock) as mock_send, \
         patch("backend.services.whatsapp.bot.whatsapp_service.send_image", new_callable=AsyncMock) as mock_img:

        mock_dl.return_value = valid_pdf_6_pages
        mock_send.return_value = {"status": "success"}
        mock_img.return_value = {"status": "success"}

        # Simulate 50 rapid uploads
        for i in range(50):
            await handle_inbound_whatsapp_event(db_session, {
                "type": "document",
                "sender": phone,
                "display_name": "Rate Limit Customer",
                "document": {"id": f"doc_{i}", "filename": f"file_{i}.pdf", "mime_type": "application/pdf"},
            })

        download_count_before = mock_dl.call_count

        # 51st upload exceeds the limit!
        mock_send.reset_mock()
        await handle_inbound_whatsapp_event(db_session, {
            "type": "document",
            "sender": phone,
            "display_name": "Rate Limit Customer",
            "document": {"id": "doc_51_overflow", "filename": "overflow.pdf", "mime_type": "application/pdf"},
        })

        # Media download was NOT called for 51st file (saves bandwidth and storage)
        assert mock_dl.call_count == download_count_before

        # Customer was sent the rate limit warning
        assert mock_send.call_count == 1
        rate_reply = mock_send.call_args[1]["text"]
        assert "Rate Limit Exceeded" in rate_reply
        assert "5 minutes" in rate_reply

    upload_rate_limiter.reset(phone)


@pytest.mark.asyncio
async def test_admin_simulate_bank_sms_endpoint(client):
    """Verifies that admin can simulate a bank credit SMS observation in YouPI-PG."""
    from backend.core.config import settings

    headers = {"X-Admin-Secret": settings.ADMIN_SECRET}
    res = await client.post(
        "/admin/payments/simulate-bank-sms",
        headers=headers,
        json={"utr": "429588889999", "amount": 15.0},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["utr"] == "429588889999"
    assert data["amount"] == 15.0

    # Test invalid UTR rejected
    bad_res = await client.post(
        "/admin/payments/simulate-bank-sms",
        headers=headers,
        json={"utr": "123", "amount": 15.0},
    )
    assert bad_res.status_code == 400
