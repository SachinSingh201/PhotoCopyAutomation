import hmac
import hashlib
import json
import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.core.config import settings
from backend.services.whatsapp.service import (
    OpenWAAdapter,
    MetaCloudAdapter,
    WhatsAppService,
    verify_openwa_signature,
    whatsapp_service,
)


def test_format_and_clean_phone():
    adapter = OpenWAAdapter()
    assert adapter._format_chat_id("+91 98765-43210") == "919876543210@c.us"
    assert adapter._format_chat_id("919876543210@c.us") == "919876543210@c.us"
    assert adapter._extract_clean_phone("919876543210@c.us") == "919876543210"
    assert adapter._extract_clean_phone("+91-98765-43210") == "919876543210"
    assert adapter._extract_clean_phone("") == ""


def test_verify_openwa_signature():
    secret = "my_openwa_test_secret"
    body = b'{"event":"message.received","data":{"body":"hello"}}'
    valid_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    # Exact hex
    assert verify_openwa_signature(body, valid_sig, secret) is True
    # With sha256= prefix
    assert verify_openwa_signature(body, f"sha256={valid_sig}", secret) is True
    # Invalid signature
    assert verify_openwa_signature(body, "invalidsignaturehex", secret) is False
    # Missing header when secret configured
    assert verify_openwa_signature(body, None, secret) is False
    # No secret configured (dev bypass)
    assert verify_openwa_signature(body, None, None) is True
    assert verify_openwa_signature(body, None, "") is True


def test_openwa_parse_text_message():
    adapter = OpenWAAdapter()
    payload = {
        "event": "message.received",
        "sessionId": "test_session",
        "data": {
            "id": "wa_msg_101",
            "from": "919876543210@c.us",
            "notifyName": "John Doe",
            "type": "chat",
            "body": "Print 2 copies double sided",
            "timestamp": 1710000000,
        },
    }

    events = adapter.parse_incoming_webhook(payload)
    assert len(events) == 1
    event = events[0]
    assert event["provider"] == "openwa"
    assert event["engine"] == "whatsapp-web.js"
    assert event["message_id"] == "wa_msg_101"
    assert event["sender"] == "919876543210"
    assert event["display_name"] == "John Doe"
    assert event["type"] == "text"
    assert event["text"] == "Print 2 copies double sided"
    assert event["session"] == "test_session"


def test_openwa_parse_document_message():
    adapter = OpenWAAdapter()
    payload = {
        "event": "message.create",
        "session": "test_session",
        "data": {
            "id": "wa_doc_202",
            "from": "919876543210@c.us",
            "pushname": "Alice",
            "type": "document",
            "filename": "thesis_chapter1.pdf",
            "mimetype": "application/pdf",
            "mediaUrl": "http://localhost:2785/api/sessions/test/media/202",
            "timestamp": 1710000010,
        },
    }

    events = adapter.parse_incoming_webhook(payload)
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "document"
    assert event["document"]["id"] == "wa_doc_202"
    assert event["document"]["filename"] == "thesis_chapter1.pdf"
    assert event["document"]["mime_type"] == "application/pdf"
    assert event["document"]["media_url"] == "http://localhost:2785/api/sessions/test/media/202"


def test_openwa_parse_button_interactive_message():
    adapter = OpenWAAdapter()
    payload = {
        "event": "message.received",
        "sessionId": "printshop_dev",
        "data": {
            "id": "wa_btn_303",
            "from": "919876543210@c.us",
            "type": "buttons_response",
            "selectedButtonId": "CONFIRM_ORDER",
            "selectedDisplayText": "Confirm & Pay",
        },
    }

    events = adapter.parse_incoming_webhook(payload)
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "interactive"
    assert event["button"]["id"] == "CONFIRM_ORDER"
    assert event["button"]["title"] == "Confirm & Pay"


def test_whatsapp_service_auto_detection():
    service = WhatsAppService()

    # Meta Cloud API payload structure
    meta_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "value": {
                            "contacts": [{"profile": {"name": "Meta User"}, "wa_id": "15551234567"}],
                            "messages": [{"from": "15551234567", "id": "meta_1", "type": "text", "text": {"body": "Hello"}}],
                        }
                    }
                ],
            }
        ],
    }
    meta_events = service.parse_incoming_webhook(meta_payload)
    assert len(meta_events) == 1
    assert meta_events[0]["provider"] == "meta"
    assert meta_events[0]["sender"] == "15551234567"

    # OpenWA payload structure
    openwa_payload = {
        "event": "message.received",
        "data": {
            "from": "919876543210@c.us",
            "id": "openwa_1",
            "type": "chat",
            "body": "Hello OpenWA",
        },
    }
    openwa_events = service.parse_incoming_webhook(openwa_payload)
    assert len(openwa_events) == 1
    assert openwa_events[0]["provider"] == "openwa"
    assert openwa_events[0]["engine"] == "whatsapp-web.js"


@pytest.mark.asyncio
async def test_openwa_send_messages_mock_fallback():
    adapter = OpenWAAdapter()
    # Sending text to an offline or test gateway gracefully falls back to mock delivery
    res = await adapter.send_text_message("919876543210", "Hello Test!")
    assert res["status"] in ["success", "mock_fallback"]
    assert res["provider"] == "openwa"

    # Sending document
    doc_res = await adapter.send_document("919876543210", "dummy.pdf", "dummy.pdf", "Your receipt")
    assert doc_res["status"] == "success"
    assert doc_res["provider"] == "openwa"

    # Downloading inline base64 media
    b64_content = "JVBERi0xLjQgVGVzdCBQREY="  # %PDF-1.4 Test PDF
    downloaded = await adapter.download_media({"base64": b64_content})
    assert downloaded.startswith(b"%PDF-1.4")


@pytest.mark.asyncio
async def test_webhook_endpoints_integration():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. GET status ping
        res_get = await ac.get("/webhooks/whatsapp")
        assert res_get.status_code == 200
        get_json = res_get.json()
        assert get_json["status"] == "active"
        assert "provider" in get_json

        # 2. GET Meta challenge handshake
        res_challenge = await ac.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "115599",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
            },
        )
        assert res_challenge.status_code == 200
        assert res_challenge.text == "115599"

        # 3. POST OpenWA event
        openwa_payload = {
            "event": "message.received",
            "sessionId": "printshop_dev",
            "data": {
                "id": "wa_integration_msg",
                "from": "919988776655@c.us",
                "notifyName": "Integration Customer",
                "type": "chat",
                "body": "Check my print order",
            },
        }
        res_post = await ac.post("/webhooks/whatsapp", json=openwa_payload)
        assert res_post.status_code == 200
        post_json = res_post.json()
        assert post_json["status"] == "received"
        assert post_json["event_count"] == 1
        assert post_json["events"][0]["sender"] == "919988776655"
