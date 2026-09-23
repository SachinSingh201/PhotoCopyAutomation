import json
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import settings
from backend.core.logging import logger
from backend.models.order import Order


class WhatsAppService:
    def __init__(self):
        self.phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.token = settings.WHATSAPP_TOKEN

    def parse_incoming_webhook(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parses WhatsApp Business Cloud API webhook payload.
        Returns a list of extracted user message/media events.
        """
        events = []
        entries = payload.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                contacts = {c["wa_id"]: c.get("profile", {}).get("name", "Customer") for c in value.get("contacts", [])}
                messages = value.get("messages", [])

                for msg in messages:
                    sender = msg.get("from")
                    msg_id = msg.get("id")
                    msg_type = msg.get("type")
                    name = contacts.get(sender, "Customer")

                    event = {
                        "message_id": msg_id,
                        "sender": sender,
                        "display_name": name,
                        "type": msg_type,
                        "timestamp": msg.get("timestamp"),
                    }

                    if msg_type == "text":
                        event["text"] = msg.get("text", {}).get("body", "")
                    elif msg_type == "document":
                        doc = msg.get("document", {})
                        event["document"] = {
                            "id": doc.get("id"),
                            "filename": doc.get("filename", "document.pdf"),
                            "mime_type": doc.get("mime_type", "application/pdf"),
                        }
                    elif msg_type == "interactive":
                        interactive = msg.get("interactive", {})
                        btn_reply = interactive.get("button_reply", {})
                        event["button"] = {
                            "id": btn_reply.get("id"),
                            "title": btn_reply.get("title"),
                        }

                    events.append(event)
        return events

    async def send_text_message(self, to_phone: str, text: str) -> Dict[str, Any]:
        """Mock/Live WhatsApp message sender."""
        logger.info(f"[WhatsApp Outbound to {to_phone}]: {text}")
        return {"status": "success", "to": to_phone, "text": text}


whatsapp_service = WhatsAppService()
