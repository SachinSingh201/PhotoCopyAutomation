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
