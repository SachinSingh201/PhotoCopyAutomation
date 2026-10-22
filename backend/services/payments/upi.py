import re
import urllib.parse
from typing import Any, Dict, List, Optional
from backend.core.config import settings

# Standard NPCI UPI VPA format: e.g. username@bank, mobile@upi
UPI_VPA_REGEX = re.compile(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z0-9.\-_]{2,64}$")


def validate_upi_id(upi_id: str) -> Dict[str, Any]:
    """
    Validates whether the provided string is a syntactically correct UPI VPA
    according to NPCI UPI specifications.
    """
    cleaned = (upi_id or "").strip()
    if not cleaned:
        return {
            "valid": False,
            "upi_id": cleaned,
            "message": "UPI ID cannot be empty. Must be in the format username@bank (e.g. shopname@okhdfcbank or 9876543210@paytm).",
        }

    if not UPI_VPA_REGEX.match(cleaned):
        return {
            "valid": False,
            "upi_id": cleaned,
            "message": f"'{cleaned}' is not a valid UPI ID. Must contain '@' and a valid provider handle (e.g. 9876543210@paytm).",
        }

    return {
        "valid": True,
        "upi_id": cleaned,
        "message": "Valid UPI Virtual Payment Address (VPA).",
    }


def generate_upi_uri(
    upi_id: Optional[str] = None,
    payee_name: Optional[str] = None,
    amount: float = 0.0,
    order_code: str = "",
    pickup_token: Optional[str] = None,
) -> str:
    """
    Constructs an NPCI-compliant UPI deep link payment URI:
    upi://pay?pa={pa}&pn={pn}&am={am}&cu=INR&tn={tn}
    """
    pa = (upi_id or settings.SHOP_UPI_ID or "printshop@upi").strip()
    pn = (payee_name or settings.SHOP_NAME or "Print Express Hub").strip()
    amt_str = f"{max(0.0, float(amount)):.2f}"

    note_parts = []
    if order_code:
        note_parts.append(f"Order #{order_code}")
    if pickup_token:
        note_parts.append(f"Token: {pickup_token}")
    note = " - ".join(note_parts) if note_parts else "Print Order Payment"

    params = {
        "pa": pa,
        "pn": pn,
        "am": amt_str,
        "cu": "INR",
        "tn": note,
    }
    encoded_params = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"upi://pay?{encoded_params}"


def generate_qr_code_url(upi_uri: str, size: int = 350) -> str:
    """
    Encodes the UPI URI into a standard QR code image URL.
    """
    encoded_data = urllib.parse.quote(upi_uri)
    return f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={encoded_data}"


def validate_qr_payload(qr_string_or_uri: str) -> Dict[str, Any]:
    """
    Inspects and validates an existing or generated QR code payload/URI.
    Checks conformance with the NPCI UPI Deep Linking specifications.
    """
    raw = (qr_string_or_uri or "").strip()
    errors: List[str] = []

    if not raw:
        return {
            "is_valid": False,
            "raw_payload": raw,
            "errors": ["QR code payload is empty."],
        }

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme.lower() != "upi":
        errors.append(f"Invalid URI scheme '{parsed.scheme}'. Expected 'upi://pay?...'")

    qs = urllib.parse.parse_qs(parsed.query)

    pa_list = qs.get("pa", [])
    pa = pa_list[0] if pa_list else ""
    if not pa:
        errors.append("Missing mandatory 'pa' (Payee Address / UPI VPA) parameter.")
    else:
        vpa_check = validate_upi_id(pa)
        if not vpa_check["valid"]:
            errors.append(vpa_check["message"])

    pn_list = qs.get("pn", [])
    pn = pn_list[0] if pn_list else ""

    am_list = qs.get("am", [])
    amount: Optional[float] = None
    if not am_list:
        errors.append("Missing 'am' (Amount) parameter.")
    else:
        try:
            amount = float(am_list[0])
            if amount < 0:
                errors.append("Amount cannot be negative.")
        except ValueError:
            errors.append(f"Invalid numeric amount '{am_list[0]}'.")

    cu_list = qs.get("cu", [])
    cu = cu_list[0] if cu_list else ""
    if cu.upper() != "INR":
        errors.append(f"Invalid currency '{cu}'. UPI requires currency 'INR'.")

    tn_list = qs.get("tn", [])
    tn = tn_list[0] if tn_list else ""

    is_valid = len(errors) == 0
    return {
        "is_valid": is_valid,
        "raw_payload": raw,
        "scheme": parsed.scheme,
        "payee_vpa": pa,
        "payee_name": pn,
        "amount": amount,
        "currency": cu,
        "transaction_note": tn,
        "errors": errors,
        "message": "Valid NPCI UPI Payment QR Payload." if is_valid else f"Validation failed with {len(errors)} error(s).",
    }
