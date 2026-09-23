import hashlib
import hmac
import re
from typing import Optional


def sanitize_filename(filename: str) -> str:
    """Strip path traversal characters and unsafe symbols while preserving extension."""
    clean = re.sub(r"[^\w\.\-\_]", "_", filename.strip())
    # remove leading dots or slashes
    clean = re.sub(r"^\.+", "", clean)
    return clean or "document.pdf"


def verify_hmac_signature(payload: bytes, secret: str, received_signature: str) -> bool:
    """Verify HMAC SHA256 signature for webhooks (WhatsApp / Payment)."""
    if not secret or not received_signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_signature)


def verify_whatsapp_signature(payload: bytes, app_secret: str, signature_header: Optional[str]) -> bool:
    """Verify WhatsApp Cloud API X-Hub-Signature-256."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    received = signature_header.split("sha256=")[1]
    return verify_hmac_signature(payload, app_secret, received)
