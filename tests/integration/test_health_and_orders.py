import pytest
from backend.core.config import settings


@pytest.mark.asyncio
async def test_health_endpoints(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

    res_ready = await client.get("/ready")
    assert res_ready.status_code == 200
    assert res_ready.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_complete_vertical_slice_order_lifecycle(client, valid_pdf_6_pages, valid_pdf_20_pages):
    """
    Tests the complete end-to-end first vertical slice from PLAN.md §115:
    1. Customer creates order
    2. Uploads 2 PDFs (6 pages and 20 pages)
    3. Natural language instruction interpreted by LLM
    4. Price calculated & confirmed
    5. Payment initiated & verified
    6. Order automatically enters print queue
    7. Local Print Agent claims job and reports success
    8. Order is marked READY_FOR_PICKUP with pickup token
    9. Admin marks order as collected
    """
    # 1. Create order
    create_res = await client.post(
        "/orders",
        json={
            "whatsapp_user_id": "wa_e2e_user",
            "phone_number": "+919876543210",
            "display_name": "Alice Johnson",
        },
    )
    assert create_res.status_code == 201
    order_data = create_res.json()
    order_id = order_data["id"]
    pickup_token = order_data["pickup_token"]
    assert order_data["status"] == "CREATED"
    assert pickup_token is not None

    # 2. Upload File 1 (6 pages)
    upload1 = await client.post(
        f"/orders/{order_id}/files",
        files={"file": ("resume.pdf", valid_pdf_6_pages, "application/pdf")},
    )
    assert upload1.status_code == 201
    assert upload1.json()["page_count"] == 6
    assert upload1.json()["upload_sequence"] == 1

    # Upload File 2 (20 pages)
    upload2 = await client.post(
        f"/orders/{order_id}/files",
        files={"file": ("assignment.pdf", valid_pdf_20_pages, "application/pdf")},
    )
    assert upload2.status_code == 201
    assert upload2.json()["page_count"] == 20
    assert upload2.json()["upload_sequence"] == 2

    # Verify order shows 26 total pages
    get_res = await client.get(f"/orders/{order_id}")
    assert get_res.status_code == 200
    assert get_res.json()["total_pages"] == 26

    # 3. LLM NLP interpretation: "Print all in black and white"
    nlp_res = await client.post(
        f"/orders/{order_id}/interpret",
        json={"text": "Print all in black and white"},
    )
    assert nlp_res.status_code == 200
    assert not nlp_res.json()["ambiguity"]

    # 4. Confirm order
    confirm_res = await client.post(f"/orders/{order_id}/confirm")
    assert confirm_res.status_code == 200
    price_snap = confirm_res.json()
    # 2 files * 5.0 base + 26 * 2.0 (BW single) = 10.0 + 52.0 = 62.0
    assert price_snap["total_amount"] == 62.0

    # 5. Initiate payment
    pay_create = await client.post("/payments/create", json={"order_id": order_id})
    assert pay_create.status_code == 200
    assert pay_create.json()["amount"] == 62.0

    # Verify payment (mock server-side proof)
    pay_verify = await client.post(
        "/payments/verify",
        json={
            "order_id": order_id,
            "provider_payment_id": "pay_mock_123456",
        },
    )
    assert pay_verify.status_code == 200
    assert pay_verify.json()["order_status"] == "QUEUED"

    # 6. Verify job appears in queue
    queue_res = await client.get("/queue")
    assert queue_res.status_code == 200
    queue_items = queue_res.json()
    assert len(queue_items) == 1
    assert queue_items[0]["order_id"] == order_id
    job_id = queue_items[0]["job_id"]

    # 7. Print Agent sends heartbeat & claims job
    agent_headers = {"Authorization": f"Bearer {settings.PRINT_AGENT_TOKEN}"}
    hb_res = await client.post(
        "/agent/heartbeat",
        headers=agent_headers,
        json={"agent_id": "agent-test-01", "status": "ONLINE", "printer_status": "READY"},
    )
    assert hb_res.status_code == 200
    assert hb_res.json()["pending_queue_count"] == 1

    claim_res = await client.post(
        "/agent/claim-job",
        headers=agent_headers,
        json={"agent_id": "agent-test-01"},
    )
    assert claim_res.status_code == 200
    claim_data = claim_res.json()
    assert claim_data["job_found"] is True
    assert claim_data["job_id"] == job_id
    assert len(claim_data["files"]) == 2

    # 8. Print Agent reports printing SUCCESS
    status_res = await client.post(
        f"/agent/jobs/{job_id}/status",
        headers=agent_headers,
        json={"agent_id": "agent-test-01", "status": "SUCCESS"},
    )
    assert status_res.status_code == 200
    assert status_res.json()["order_status"] == "READY_FOR_PICKUP"

    # 9. Admin marks order as collected
    admin_headers = {"X-Admin-Secret": settings.ADMIN_SECRET}
    collect_res = await client.post(
        f"/admin/orders/{order_id}/collected",
        headers=admin_headers,
    )
    assert collect_res.status_code == 200
    assert collect_res.json()["state"] == "COLLECTED"
