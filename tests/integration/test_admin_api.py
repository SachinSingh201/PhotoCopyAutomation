import pytest
from httpx import AsyncClient
from backend.core.config import settings
from backend.models.notification import Notification
from backend.models.order import Order
from backend.models.printer import Printer
from backend.models.queue import PrintJob, QueueEntry
from backend.models.user import User


@pytest.fixture
def admin_headers():
    return {"X-Admin-Secret": settings.ADMIN_SECRET}


@pytest.mark.asyncio
async def test_admin_auth_required(client: AsyncClient):
    res = await client.get("/admin/dashboard")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_dashboard_and_html(client: AsyncClient, admin_headers):
    # 1. HTML Admin Dashboard interface
    html_res = await client.get("/admin")
    assert html_res.status_code == 200
    assert "PRINTSHOP" in html_res.text

    # 2. JSON Dashboard Stats
    api_res = await client.get("/admin/dashboard", headers=admin_headers)
    assert api_res.status_code == 200
    data = api_res.json()
    assert "orders_today" in data
    assert "effective_print_status" in data
    assert "revenue_today" in data


@pytest.mark.asyncio
async def test_admin_system_status_and_search(client: AsyncClient, db_session, admin_headers):
    # Setup test user and order
    user = User(whatsapp_user_id="wa_search_user", phone_number="+919876540000", display_name="Search User")
    db_session.add(user)
    await db_session.flush()

    order = Order(order_code="ORD-SEARCH-1", pickup_token="S101", user_id=user.id, status="QUEUED", total_pages=4, total_amount=18.0)
    db_session.add(order)
    await db_session.commit()

    # 1. System status
    sys_res = await client.get("/admin/system/status", headers=admin_headers)
    assert sys_res.status_code == 200
    sys_data = sys_res.json()
    assert "agent_status" in sys_data
    assert "printers_connected" in sys_data

    # 2. Global search by token
    search_res = await client.get("/admin/search?q=S101", headers=admin_headers)
    assert search_res.status_code == 200
    results = search_res.json()
    assert len(results) >= 1
    assert "ORD-SEARCH-1" in results[0]["title"]


@pytest.mark.asyncio
async def test_admin_notifications_and_alerts(client: AsyncClient, db_session, admin_headers):
    notif = Notification(
        title="Payment Alert",
        message="Payment verified for order ORD-1234",
        type="PAYMENT_RECEIVED",
        is_read=False,
    )
    db_session.add(notif)
    await db_session.commit()

    # 1. List notifications
    res = await client.get("/admin/notifications", headers=admin_headers)
    assert res.status_code == 200
    notifs = res.json()
    assert len(notifs) >= 1

    # 2. Mark read
    patch_res = await client.patch(f"/admin/notifications/{notif.id}/read", headers=admin_headers)
    assert patch_res.status_code == 200

    # 3. List open alerts
    alert_res = await client.get("/admin/alerts?status=open", headers=admin_headers)
    assert alert_res.status_code == 200


@pytest.mark.asyncio
async def test_admin_printers_management(client: AsyncClient, db_session, admin_headers):
    # 1. List printers
    res = await client.get("/admin/printers", headers=admin_headers)
    assert res.status_code == 200
    printers = res.json()
    assert len(printers) >= 1
    printer_id = printers[0]["id"]

    # 2. Scan printers
    scan_res = await client.post("/admin/printers/scan", headers=admin_headers)
    assert scan_res.status_code == 200

    # 3. Test page
    test_res = await client.post(f"/admin/printers/{printer_id}/test-page", headers=admin_headers)
    assert test_res.status_code == 200

    # 4. Set default
    patch_res = await client.patch(f"/admin/printers/{printer_id}", headers=admin_headers, json={"is_default": True})
    assert patch_res.status_code == 200
    assert patch_res.json()["is_default"] is True


@pytest.mark.asyncio
async def test_admin_reports_and_settings(client: AsyncClient, admin_headers):
    # 1. Reports summary
    rep_res = await client.get("/admin/reports/summary", headers=admin_headers)
    assert rep_res.status_code == 200
    rep_data = rep_res.json()
    assert "total_revenue_7d" in rep_data
    assert "daily_revenue" in rep_data

    # 2. Shop settings
    shop_res = await client.get("/admin/settings/shop", headers=admin_headers)
    assert shop_res.status_code == 200

    update_res = await client.put(
        "/admin/settings/shop",
        headers=admin_headers,
        json={
            "shop_name": "QuickPrint Express",
            "timezone": "Asia/Kolkata",
            "currency": "INR",
            "order_acceptance_enabled": True,
        },
    )
    assert update_res.status_code == 200
    assert update_res.json()["settings"]["shop_name"] == "QuickPrint Express"
