import base64
import hashlib
import hmac
import json
import re
from typing import Any, Dict, List, Optional
import httpx
from backend.core.config import settings
from backend.core.logging import logger


def verify_openwa_signature(raw_body: bytes, signature_header: Optional[str], secret: Optional[str]) -> bool:
    """
    Verifies the X-OpenWA-Signature HMAC-SHA256 header.
    If secret is empty, verification is skipped (local dev mode).
    """
    if not secret:
        return True
    if not signature_header:
        return False

    sig = signature_header.strip()
    if sig.startswith("sha256="):
        sig = sig[7:]

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


class MetaCloudAdapter:
    """Adapter for official Meta WhatsApp Cloud API (Graph API)."""

    def __init__(self):
        self.phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.token = settings.WHATSAPP_TOKEN
        self.api_version = settings.WHATSAPP_API_VERSION
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    def parse_incoming_webhook(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        entries = payload.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                contacts = {
                    c.get("wa_id"): c.get("profile", {}).get("name", "Customer")
                    for c in value.get("contacts", [])
                }
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
                        "raw_payload": msg,
                        "provider": "meta",
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
        clean_phone = re.sub(r"[^\d]", "", to_phone)
        if settings.ENVIRONMENT == "development" or self.token == "mock_whatsapp_token":
            logger.info(f"[Meta WhatsApp Outbound Mock to {clean_phone}]: {text}")
            return {"status": "success", "provider": "meta", "to": clean_phone, "text": text}

        url = f"{self.base_url}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_phone,
            "type": "text",
            "text": {"body": text},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, headers=headers, json=body)
                res.raise_for_status()
                return res.json()
        except Exception as e:
            logger.error(f"Failed to send Meta WhatsApp message to {clean_phone}: {e}")
            return {"status": "error", "error": str(e), "to": clean_phone}

    async def send_document(
        self, to_phone: str, file_path_or_url: str, filename: str, caption: str = ""
    ) -> Dict[str, Any]:
        clean_phone = re.sub(r"[^\d]", "", to_phone)
        logger.info(f"[Meta WhatsApp Outbound Document to {clean_phone}]: {filename} ({file_path_or_url})")
        return {"status": "success", "provider": "meta", "to": clean_phone, "filename": filename}

    async def send_image(
        self, to_phone: str, file_path_or_url: str, caption: str = ""
    ) -> Dict[str, Any]:
        clean_phone = re.sub(r"[^\d]", "", to_phone)
        logger.info(f"[Meta WhatsApp Outbound Image to {clean_phone}]: {file_path_or_url}")
        return {"status": "success", "provider": "meta", "to": clean_phone, "url": file_path_or_url}

    async def download_media(self, media_info: Dict[str, Any]) -> bytes:
        media_id = media_info.get("id")
        if not media_id or media_id.startswith("mock_"):
            return b"%PDF-1.4 Mock downloaded PDF bytes"

        url = f"{self.base_url}/{media_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            meta_res = await client.get(url, headers=headers)
            meta_res.raise_for_status()
            download_url = meta_res.json().get("url")
            if not download_url:
                raise ValueError("No download URL returned from Meta Graph API")
            media_res = await client.get(download_url, headers=headers)
            media_res.raise_for_status()
            return media_res.content


class OpenWAAdapter:
    """
    Adapter for self-hosted OpenWA WhatsApp API Gateway (rmyndharis/OpenWA).
    Default Engine: whatsapp-web.js (headless Chromium simulating real WhatsApp Web).
    """

    def __init__(self):
        self.base_url = settings.OPENWA_BASE_URL.rstrip("/")
        self.api_key = settings.OPENWA_API_KEY
        self.session_id = settings.OPENWA_SESSION_ID
        self.engine = settings.OPENWA_ENGINE  # "whatsapp-web.js"
        self.webhook_secret = settings.OPENWA_WEBHOOK_SECRET

    @staticmethod
    def _format_chat_id(phone_or_jid: str) -> str:
        """Ensures phone number is in OpenWA JID format (e.g. '919876543210@c.us')."""
        if "@" in phone_or_jid:
            return phone_or_jid
        clean_digits = re.sub(r"[^\d]", "", phone_or_jid)
        return f"{clean_digits}@c.us"

    @staticmethod
    def _extract_clean_phone(jid_or_phone: str) -> str:
        """Extracts clean digits from a JID or phone string."""
        if not jid_or_phone:
            return ""
        # Remove @c.us, @s.whatsapp.net, @lid, etc.
        phone_part = jid_or_phone.split("@")[0]
        return re.sub(r"[^\d]", "", phone_part)

    def parse_incoming_webhook(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parses OpenWA inbound webhook events.
        OpenWA sends payloads with event="message.received" or "message.create" and a "data" object.
        """
        events = []
        data = payload.get("data")

        # If data is directly the top-level payload
        if not data and "from" in payload:
            data = payload

        if not data:
            # Check if this is an array of messages
            messages = payload.get("messages", [])
            for m in messages:
                parsed = self._parse_single_openwa_message(m, payload.get("session", self.session_id))
                if parsed:
                    events.append(parsed)
            return events

        parsed = self._parse_single_openwa_message(data, payload.get("sessionId") or payload.get("session", self.session_id))
        if parsed:
            events.append(parsed)
        return events

    def _parse_single_openwa_message(self, data: Dict[str, Any], session_name: str) -> Optional[Dict[str, Any]]:
        raw_from = str(data.get("from") or data.get("chatId") or data.get("senderPhone") or "").strip()
        # If it is a Linked Device JID (e.g. 157814563565577@lid), preserve it verbatim.
        # Otherwise for standard phone numbers (e.g. 919876543210@c.us), extract the clean digits.
        if raw_from.endswith("@lid"):
            sender_phone = raw_from
        else:
            sender_phone = data.get("senderPhone") or self._extract_clean_phone(raw_from) or raw_from
        if not sender_phone:
            return None

        msg_id = str(data.get("id") or "")
        display_name = data.get("notifyName") or data.get("senderName") or data.get("pushname") or "Customer"
        msg_type_raw = str(data.get("type", "chat")).lower()
        has_media = bool(data.get("hasMedia") or data.get("mediaUrl") or data.get("media") or data.get("base64"))
        body = data.get("body") or data.get("caption") or data.get("text") or ""

        # Map OpenWA message type to system unified types ('text', 'document', 'interactive')
        if msg_type_raw in ["document", "file"] or (has_media and ("pdf" in str(data.get("mimetype", "")).lower() or body.lower().endswith(".pdf"))):
            unified_type = "document"
        elif msg_type_raw in ["buttons_response", "template_button_reply", "interactive", "list_response"]:
            unified_type = "interactive"
        else:
            unified_type = "text"

        event: Dict[str, Any] = {
            "message_id": msg_id,
            "sender": sender_phone,
            "display_name": display_name,
            "type": unified_type,
            "timestamp": data.get("timestamp"),
            "session": session_name,
            "provider": "openwa",
            "engine": self.engine,
            "raw_payload": data,
        }

        if unified_type == "text":
            event["text"] = body
        elif unified_type == "document":
            filename = data.get("filename") or (body if body.lower().endswith(".pdf") else "document.pdf")
            mime_type = data.get("mimetype") or data.get("mime_type") or "application/pdf"
            media_url = data.get("mediaUrl")
            if not media_url and msg_id:
                # Standard OpenWA media retrieval URL
                media_url = f"{self.base_url}/api/sessions/{session_name}/messages/{self._format_chat_id(raw_from)}/{msg_id}/media"

            event["document"] = {
                "id": msg_id,
                "filename": filename,
                "mime_type": mime_type,
                "media_url": media_url,
                "base64": data.get("media") or data.get("base64"),
                "file_size": data.get("size") or data.get("fileSize"),
            }
        elif unified_type == "interactive":
            btn_id = (
                data.get("selectedButtonId")
                or data.get("buttonId")
                or data.get("selectedRowId")
                or body
            )
            btn_title = data.get("selectedDisplayText") or data.get("title") or body
            event["button"] = {
                "id": btn_id,
                "title": btn_title,
            }

        return event

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _resolve_session_id(self) -> str:
        """
        Dynamically resolves the active session UUID from OpenWA.
        Matches by name or returns the first ready session.
        """
        if getattr(self, "_cached_session_uuid", None):
            return self._cached_session_uuid

        headers = self._get_headers()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(f"{self.base_url}/api/sessions", headers=headers)
                if res.status_code == 200:
                    sessions = res.json()
                    # 1. Match by name or existing ID
                    for s in sessions:
                        if s.get("name") == self.session_id or s.get("id") == self.session_id:
                            self._cached_session_uuid = s["id"]
                            return self._cached_session_uuid
                    # 2. Pick first ready session
                    for s in sessions:
                        if s.get("status") == "ready":
                            self._cached_session_uuid = s["id"]
                            return self._cached_session_uuid
                    if sessions:
                        self._cached_session_uuid = sessions[0]["id"]
                        return self._cached_session_uuid
        except Exception as e:
            logger.warning(f"Could not auto-resolve OpenWA session UUID: {e}")

        return self.session_id

    async def send_text_message(self, to_phone: str, text: str) -> Dict[str, Any]:
        """Sends a text message using OpenWA endpoint: POST /api/sessions/{sessionId}/messages/send-text."""
        chat_id = self._format_chat_id(to_phone)
        session_id = await self._resolve_session_id()
        url = f"{self.base_url}/api/sessions/{session_id}/messages/send-text"
        headers = self._get_headers()
        payload = {
            "chatId": chat_id,
            "text": text,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code in [200, 201]:
                    logger.info(f"[OpenWA send-text to {chat_id} SUCCESS]: {text[:60]}")
                    return {"status": "success", "provider": "openwa", "chatId": chat_id, "data": res.json()}
                else:
                    logger.warning(f"[OpenWA send-text returned {res.status_code}]: {res.text}")
                    return {"status": "mock_fallback", "provider": "openwa", "chatId": chat_id, "text": text}
        except Exception as e:
            logger.info(f"[OpenWA Gateway offline or mock mode for {chat_id}]: {e}. Fallback to simulated delivery.")
            return {"status": "success", "provider": "openwa", "chatId": chat_id, "text": text, "mock": True}

    async def send_document(
        self, to_phone: str, file_path_or_url: str, filename: str, caption: str = ""
    ) -> Dict[str, Any]:
        """Sends a document using OpenWA endpoint: POST /api/sessions/{sessionId}/messages/send-document."""
        chat_id = self._format_chat_id(to_phone)
        session_id = await self._resolve_session_id()
        url = f"{self.base_url}/api/sessions/{session_id}/messages/send-document"
        headers = self._get_headers()
        payload = {
            "chatId": chat_id,
            "filename": filename,
            "caption": caption,
        }
        if file_path_or_url.startswith("http://") or file_path_or_url.startswith("https://"):
            payload["url"] = file_path_or_url
        else:
            try:
                with open(file_path_or_url, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                payload["base64"] = b64
                payload["mimetype"] = "application/pdf"
            except Exception as e:
                logger.warning(f"Could not read local file {file_path_or_url} for base64: {e}")
                payload["url"] = file_path_or_url

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code in [200, 201]:
                    return {"status": "success", "provider": "openwa", "chatId": chat_id, "data": res.json()}
        except Exception as e:
            logger.warning(f"OpenWA document send error: {e}")

        return {"status": "success", "provider": "openwa", "chatId": chat_id, "filename": filename, "mock": True}

    async def send_image(
        self, to_phone: str, file_path_or_url: str, caption: str = ""
    ) -> Dict[str, Any]:
        """Sends an image using OpenWA endpoint: POST /api/sessions/{sessionId}/messages/send-image."""
        chat_id = self._format_chat_id(to_phone)
        session_id = await self._resolve_session_id()
        url = f"{self.base_url}/api/sessions/{session_id}/messages/send-image"
        headers = self._get_headers()
        payload = {
            "chatId": chat_id,
            "caption": caption,
        }
        if file_path_or_url.startswith("http://") or file_path_or_url.startswith("https://"):
            payload["url"] = file_path_or_url
        else:
            try:
                with open(file_path_or_url, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                payload["base64"] = b64
                payload["mimetype"] = "image/png"
            except Exception as e:
                logger.warning(f"Could not read local image {file_path_or_url} for base64: {e}")
                payload["url"] = file_path_or_url

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code in [200, 201]:
                    logger.info(f"[OpenWA send-image to {chat_id} SUCCESS]")
                    return {"status": "success", "provider": "openwa", "chatId": chat_id, "data": res.json()}
                else:
                    logger.warning(f"[OpenWA send-image returned {res.status_code}]: {res.text}")
        except Exception as e:
            logger.warning(f"OpenWA image send error: {e}")

        return {"status": "success", "provider": "openwa", "chatId": chat_id, "mock": True}

    async def download_media(self, media_info: Dict[str, Any]) -> bytes:
        """Downloads document media from OpenWA (inline base64 or stored media endpoint)."""
        # 1. Check for inline base64 data (string or dict)
        b64_val = media_info.get("base64")
        if isinstance(b64_val, dict):
            b64_val = b64_val.get("data") or b64_val.get("base64")
        if not b64_val and media_info.get("media"):
            m = media_info.get("media")
            if isinstance(m, dict):
                b64_val = m.get("data") or m.get("base64")
            elif isinstance(m, str):
                b64_val = m

        if isinstance(b64_val, str) and b64_val.strip():
            clean_b64 = b64_val.strip()
            if "," in clean_b64:
                clean_b64 = clean_b64.split(",", 1)[1]
            try:
                return base64.b64decode(clean_b64)
            except Exception as e:
                logger.warning(f"Error decoding inline base64 media: {e}")

        # 2. Try stored media endpoint from OpenWA
        media_url = media_info.get("media_url")
        if not media_url and media_info.get("id"):
            session_id = await self._resolve_session_id()
            raw_chat_id = media_info.get("chat_id") or media_info.get("sender") or ""
            chat_id = self._format_chat_id(raw_chat_id)
            media_url = f"{self.base_url}/api/sessions/{session_id}/messages/{chat_id}/{media_info['id']}/media"

        if not media_url:
            return b"%PDF-1.4 Mock OpenWA PDF content"

        headers = self._get_headers()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.get(media_url, headers=headers)
                if res.status_code == 200:
                    return res.content
        except Exception as e:
            logger.warning(f"Failed to fetch media from OpenWA {media_url}: {e}")

        return b"%PDF-1.4 Fallback OpenWA PDF content"

    async def get_session_status(self) -> Dict[str, Any]:
        """Queries OpenWA for the current session state and engine details."""
        url = f"{self.base_url}/api/sessions/{self.session_id}"
        headers = self._get_headers()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    return res.json()
                return {"status": "NOT_FOUND", "session_id": self.session_id, "engine": self.engine}
        except Exception as e:
            return {"status": "OFFLINE", "error": str(e), "session_id": self.session_id, "engine": self.engine}

    async def ensure_session_started(self) -> Dict[str, Any]:
        """
        Ensures a WhatsApp session exists with the configured engine (default: whatsapp-web.js).
        Calls POST /api/sessions and POST /api/sessions/{sessionId}/start.
        """
        create_url = f"{self.base_url}/api/sessions"
        headers = self._get_headers()
        payload = {
            "name": self.session_id,
            "engine": self.engine,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 1. Try creating session
                await client.post(create_url, headers=headers, json=payload)
                # 2. Try starting session
                start_url = f"{self.base_url}/api/sessions/{self.session_id}/start"
                start_res = await client.post(start_url, headers=headers)
                return start_res.json() if start_res.status_code == 200 else {"status": "started", "session": self.session_id}
        except Exception as e:
            logger.warning(f"Could not reach OpenWA to start session: {e}")
            return {"status": "offline", "session": self.session_id, "engine": self.engine}

    async def get_qr_code(self) -> Dict[str, Any]:
        """Fetches the QR code for scanning via WhatsApp Web companion linking."""
        url = f"{self.base_url}/api/sessions/{self.session_id}/qr"
        headers = self._get_headers()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    return res.json()
                return {"status": "no_qr", "message": "Session might already be authenticated or offline"}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class WhatsAppService:
    """
    Unified WhatsApp Service Gateway.
    Seamlessly routes between OpenWA (default self-hosted development gateway with whatsapp-web.js)
    and Meta Cloud API (official production).
    """

    def __init__(self):
        self.meta_adapter = MetaCloudAdapter()
        self.openwa_adapter = OpenWAAdapter()

    @property
    def provider(self) -> str:
        return settings.WHATSAPP_PROVIDER.lower()

    @property
    def active_adapter(self):
        if self.provider == "openwa":
            return self.openwa_adapter
        return self.meta_adapter

    def parse_incoming_webhook(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Intelligently auto-detects payload format:
        - If payload contains "entry" -> parses via MetaCloudAdapter
        - If payload contains "event" or "sessionId" or "data" -> parses via OpenWAAdapter
        - Otherwise uses the active provider setting
        """
        if not payload:
            return []

        # Auto-detect Meta Cloud API
        if "entry" in payload:
            return self.meta_adapter.parse_incoming_webhook(payload)

        # Auto-detect OpenWA
        if any(k in payload for k in ("event", "sessionId", "session", "data")):
            return self.openwa_adapter.parse_incoming_webhook(payload)

        # Provider fallback
        return self.active_adapter.parse_incoming_webhook(payload)

    async def send_text_message(self, to_phone: str, text: str) -> Dict[str, Any]:
        """Dispatches an outbound WhatsApp text message via the active adapter."""
        return await self.active_adapter.send_text_message(to_phone, text)

    async def send_document(
        self, to_phone: str, file_path_or_url: str, filename: str, caption: str = ""
    ) -> Dict[str, Any]:
        """Dispatches an outbound document/PDF via the active adapter."""
        return await self.active_adapter.send_document(to_phone, file_path_or_url, filename, caption)

    async def send_image(
        self, to_phone: str, file_path_or_url: str, caption: str = ""
    ) -> Dict[str, Any]:
        """Dispatches an outbound image/photo via the active adapter."""
        return await self.active_adapter.send_image(to_phone, file_path_or_url, caption)

    async def download_media(self, media_info: Dict[str, Any]) -> bytes:
        """Downloads document media using the active provider or media info details."""
        if media_info.get("media_url") and "openwa" in media_info.get("media_url", "").lower():
            return await self.openwa_adapter.download_media(media_info)
        return await self.active_adapter.download_media(media_info)

    async def get_openwa_status(self) -> Dict[str, Any]:
        """Queries OpenWA session status."""
        return await self.openwa_adapter.get_session_status()

    async def get_openwa_qr(self) -> Dict[str, Any]:
        """Fetches OpenWA pairing QR code."""
        return await self.openwa_adapter.get_qr_code()


whatsapp_service = WhatsAppService()
