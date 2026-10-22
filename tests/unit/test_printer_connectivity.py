"""
Tests: printer connectivity module (PrinterDriver) + Print Agent daemon mechanics.

Covers:
  - PrinterDriver.print_file mock execution (success path)
  - PrinterDriver always returns True in mock mode
  - PrintAgentDaemon heartbeat send/receive (against live test HTTP server)
  - PrintAgentDaemon claim-job round-trip (job_found=False when queue empty)
  - PrintAgentDaemon job status update (SUCCESS / FAILED paths)
  - Agent token auth enforcement on /agent/* endpoints
  - Agent config values load correctly
"""

import asyncio
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient, Response

from print_agent.config import agent_settings
from print_agent.printer import PrinterDriver


# ─── PrinterDriver ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_printer_driver_print_file_success():
    """PrinterDriver.print_file returns True in mock mode."""
    driver = PrinterDriver()
    with patch("print_agent.printer.agent_settings") as mock_cfg:
        mock_cfg.MOCK_PRINT_EXECUTION_TIME_SECONDS = 0  # instant in tests
        result = await driver.print_file(
            file_path="/storage/private/test.pdf",
            page_count=5,
            color_mode="bw",
            default_sides="single",
            rules_json="[]",
        )
    assert result is True


@pytest.mark.asyncio
async def test_printer_driver_print_file_color_duplex():
    """PrinterDriver handles color+duplex params without error."""
    driver = PrinterDriver()
    with patch("print_agent.printer.agent_settings") as mock_cfg:
        mock_cfg.MOCK_PRINT_EXECUTION_TIME_SECONDS = 0
        result = await driver.print_file(
            file_path="/storage/private/color_doc.pdf",
            page_count=12,
            color_mode="color",
            default_sides="double",
            rules_json='[{"pages":"1-5","color":"color"},{"pages":"6-12","color":"bw"}]',
        )
    assert result is True


@pytest.mark.asyncio
async def test_printer_driver_always_succeeds_mock():
    """Mock driver never returns False — no hardware error simulation exists yet."""
    driver = PrinterDriver()
    with patch("print_agent.printer.agent_settings") as mock_cfg:
        mock_cfg.MOCK_PRINT_EXECUTION_TIME_SECONDS = 0
        for _ in range(5):
            result = await driver.print_file(
                file_path=f"/storage/private/file_{_}.pdf",
                page_count=1,
                color_mode="bw",
                default_sides="single",
                rules_json="[]",
            )
            assert result is True


@pytest.mark.asyncio
async def test_printer_driver_zero_page_count():
    """PrinterDriver accepts zero page_count (degenerate input) without crash."""
    driver = PrinterDriver()
    with patch("print_agent.printer.agent_settings") as mock_cfg:
        mock_cfg.MOCK_PRINT_EXECUTION_TIME_SECONDS = 0
        result = await driver.print_file(
            file_path="/storage/private/empty.pdf",
            page_count=0,
            color_mode="bw",
            default_sides="single",
            rules_json="[]",
        )
    assert result is True


# ─── Agent Config ─────────────────────────────────────────────────────────────

def test_agent_config_defaults():
    """AgentSettings load expected defaults."""
    assert agent_settings.BACKEND_URL == "http://localhost:8000"
    assert agent_settings.PRINT_AGENT_TOKEN == "print_agent_secret_token_123"
    assert agent_settings.PRINT_AGENT_ID == "agent-local-01"
    assert agent_settings.HEARTBEAT_INTERVAL_SECONDS == 15
    assert agent_settings.POLL_INTERVAL_SECONDS == 5
    assert agent_settings.MOCK_PRINT_EXECUTION_TIME_SECONDS == 2


# ─── Agent Heartbeat endpoint ─────────────────────────────────────────────────

AGENT_HEADERS = {"Authorization": f"Bearer {agent_settings.PRINT_AGENT_TOKEN}"}
BAD_HEADERS = {"Authorization": "Bearer wrong_token"}


@pytest.mark.asyncio
async def test_agent_heartbeat_success(client):
    """POST /agent/heartbeat with valid token registers agent and returns acknowledgment."""
    payload = {
        "agent_id": "agent-local-01",
        "status": "ONLINE",
        "printer_status": "READY",
    }
    res = await client.post("/agent/heartbeat", headers=AGENT_HEADERS, json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["acknowledged"] is True
    assert "server_time" in data
    assert isinstance(data["pending_queue_count"], int)
    assert data["pending_queue_count"] == 0  # empty DB


@pytest.mark.asyncio
async def test_agent_heartbeat_idempotent(client):
    """Repeated heartbeats update in-place, not create duplicates."""
    payload = {
        "agent_id": "agent-idem-01",
        "status": "ONLINE",
        "printer_status": "READY",
    }
    for _ in range(3):
        res = await client.post("/agent/heartbeat", headers=AGENT_HEADERS, json=payload)
        assert res.status_code == 200
        assert res.json()["acknowledged"] is True


@pytest.mark.asyncio
async def test_agent_heartbeat_different_printer_statuses(client):
    """Heartbeat accepts all valid printer_status values."""
    for printer_status in ["READY", "PRINTING", "ERROR", "OUT_OF_PAPER"]:
        res = await client.post(
            "/agent/heartbeat",
            headers=AGENT_HEADERS,
            json={
                "agent_id": "agent-local-01",
                "status": "ONLINE",
                "printer_status": printer_status,
            },
        )
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_agent_heartbeat_with_metadata(client):
    """Heartbeat accepts optional metadata dict."""
    payload = {
        "agent_id": "agent-local-01",
        "status": "ONLINE",
        "printer_status": "READY",
        "metadata": {"toner_level": 72, "paper_tray": "A4"},
    }
    res = await client.post("/agent/heartbeat", headers=AGENT_HEADERS, json=payload)
    assert res.status_code == 200


# ─── Agent token auth ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_heartbeat_wrong_token_rejected(client):
    """Wrong bearer token gets HTTP 403."""
    res = await client.post(
        "/agent/heartbeat",
        headers=BAD_HEADERS,
        json={"agent_id": "agent-local-01", "status": "ONLINE", "printer_status": "READY"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_agent_heartbeat_no_auth_rejected(client):
    """Missing Authorization header gets HTTP 422 (validation error)."""
    res = await client.post(
        "/agent/heartbeat",
        json={"agent_id": "agent-local-01", "status": "ONLINE", "printer_status": "READY"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_agent_claim_job_wrong_token_rejected(client):
    """Wrong token on claim-job gets HTTP 403."""
    res = await client.post(
        "/agent/claim-job",
        headers=BAD_HEADERS,
        json={"agent_id": "agent-local-01"},
    )
    assert res.status_code == 403


# ─── Claim-job endpoint (empty queue) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_claim_job_empty_queue(client, db_session):
    """Claim-job returns job_found=False on empty queue."""
    # Seed schedule so shop is considered open
    from backend.services.scheduling.service import seed_default_schedule_if_empty
    await seed_default_schedule_if_empty(db_session)

    res = await client.post(
        "/agent/claim-job",
        headers=AGENT_HEADERS,
        json={"agent_id": "agent-local-01"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["job_found"] is False
    assert data["job_id"] is None
    assert data["files"] == []


# ─── Full claim + status update round-trip ────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_full_job_cycle_success(client, db_session):
    """
    Full round-trip: enqueue a real PrintJob, claim it via agent API,
    then mark SUCCESS and verify order reaches READY_FOR_PICKUP.
    """
    from datetime import datetime, timezone
    from backend.models.user import User
    from backend.models.order import Order
    from backend.models.file import OrderFile
    from backend.models.configuration import PrintConfiguration
    from backend.models.queue import PrintJob, QueueEntry
    from backend.models.schedule import PrintServiceControl, PrintingSchedule
    from datetime import time as dt_time

    # Seed schedule + force shop open
    for day in range(7):
        db_session.add(
            PrintingSchedule(day_of_week=day, open_time=dt_time(0, 0), close_time=dt_time(23, 59), enabled=True)
        )
    db_session.add(
        PrintServiceControl(
            manual_override="AUTO",
            effective_status="RUNNING",
            reason="Test forced open",
            updated_by="test",
        )
    )

    # Create user + order + file + config
    user = User(whatsapp_user_id="wa_print_001", phone_number="+919000000001")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        order_code="ORD-PRNT-001",
        user_id=user.id,
        status="PAID",
        total_pages=4,
        pickup_token="T001",
    )
    db_session.add(order)
    await db_session.flush()

    order_file = OrderFile(
        order_id=order.id,
        upload_sequence=1,
        original_filename="test.pdf",
        file_hash="abc123",
        storage_path="/storage/private/test.pdf",
        page_count=4,
        file_size=10000,
        status="VALIDATED",
    )
    db_session.add(order_file)
    await db_session.flush()

    config = PrintConfiguration(
        order_id=order.id,
        file_id=order_file.id,
        color_mode="bw",
        default_sides="single",
        rules_json="[]",
    )
    db_session.add(config)
    await db_session.flush()

    # Enqueue
    from backend.services.queue.service import enqueue_verified_order
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    # Verify job is QUEUED
    assert job.status == "QUEUED"
    assert job.queue_position == 1

    # Claim via API
    claim_res = await client.post(
        "/agent/claim-job",
        headers=AGENT_HEADERS,
        json={"agent_id": "agent-local-01"},
    )
    assert claim_res.status_code == 200
    claim_data = claim_res.json()
    assert claim_data["job_found"] is True
    assert claim_data["job_id"] == job.id
    assert claim_data["order_code"] == "ORD-PRNT-001"
    assert claim_data["pickup_token"] == "T001"
    assert len(claim_data["files"]) == 1
    assert claim_data["files"][0]["page_count"] == 4
    assert claim_data["files"][0]["color_mode"] == "bw"

    # Report SUCCESS
    status_res = await client.post(
        f"/agent/jobs/{job.id}/status",
        headers=AGENT_HEADERS,
        json={"agent_id": "agent-local-01", "status": "SUCCESS"},
    )
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["status"] == "PRINTED"
    assert status_data["order_status"] == "READY_FOR_PICKUP"


@pytest.mark.asyncio
async def test_agent_full_job_cycle_failure_then_manual_required(client, db_session):
    """
    FAILED status reported twice triggers MANUAL_REQUIRED via auto-retry exhaustion.
    """
    from datetime import time as dt_time
    from backend.models.user import User
    from backend.models.order import Order
    from backend.models.file import OrderFile
    from backend.models.configuration import PrintConfiguration
    from backend.models.schedule import PrintServiceControl, PrintingSchedule
    from backend.services.queue.service import enqueue_verified_order

    # Seed open schedule
    for day in range(7):
        db_session.add(
            PrintingSchedule(day_of_week=day, open_time=dt_time(0, 0), close_time=dt_time(23, 59), enabled=True)
        )
    db_session.add(
        PrintServiceControl(manual_override="AUTO", effective_status="RUNNING", reason="test", updated_by="test")
    )

    user = User(whatsapp_user_id="wa_fail_001", phone_number="+919000000002")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        order_code="ORD-FAIL-001",
        user_id=user.id,
        status="PAID",
        total_pages=2,
        pickup_token="T002",
    )
    db_session.add(order)
    await db_session.flush()

    order_file = OrderFile(
        order_id=order.id,
        upload_sequence=1,
        original_filename="fail.pdf",
        file_hash="fail_hash",
        storage_path="/storage/private/fail.pdf",
        page_count=2,
        file_size=5000,
        status="VALIDATED",
    )
    db_session.add(order_file)
    await db_session.flush()

    db_session.add(
        PrintConfiguration(
            order_id=order.id, file_id=order_file.id, color_mode="bw", default_sides="single", rules_json="[]"
        )
    )
    await db_session.flush()

    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    # First claim + FAILED
    claim1 = await client.post("/agent/claim-job", headers=AGENT_HEADERS, json={"agent_id": "agent-local-01"})
    assert claim1.json()["job_found"] is True
    job_id = claim1.json()["job_id"]

    f1 = await client.post(
        f"/agent/jobs/{job_id}/status",
        headers=AGENT_HEADERS,
        json={"agent_id": "agent-local-01", "status": "FAILED", "error_code": "PRINT_JAM"},
    )
    assert f1.status_code == 200
    # After first failure: PRINT_FAILED, order = PRINT_FAILED (not yet MANUAL_REQUIRED)
    assert f1.json()["status"] == "FAILED"

    # Re-queue via retry, then claim again + FAILED (2nd attempt)
    from backend.services.queue.service import retry_job
    await retry_job(db_session, job_id, actor_id="test")
    await db_session.commit()  # must commit so API route sees updated state

    claim2 = await client.post("/agent/claim-job", headers=AGENT_HEADERS, json={"agent_id": "agent-local-01"})
    assert claim2.json()["job_found"] is True

    f2 = await client.post(
        f"/agent/jobs/{job_id}/status",
        headers=AGENT_HEADERS,
        json={"agent_id": "agent-local-01", "status": "FAILED", "error_code": "OUT_OF_PAPER"},
    )
    assert f2.status_code == 200
    assert f2.json()["status"] == "FAILED"
    # 2 attempts exhausted -> MANUAL_REQUIRED
    assert f2.json()["order_status"] == "MANUAL_REQUIRED"


@pytest.mark.asyncio
async def test_agent_job_status_update_unknown(client, db_session):
    """UNKNOWN status transitions order to PRINT_RESULT_UNKNOWN."""
    from datetime import time as dt_time
    from backend.models.user import User
    from backend.models.order import Order
    from backend.models.file import OrderFile
    from backend.models.configuration import PrintConfiguration
    from backend.models.schedule import PrintServiceControl, PrintingSchedule
    from backend.services.queue.service import enqueue_verified_order

    for day in range(7):
        db_session.add(
            PrintingSchedule(day_of_week=day, open_time=dt_time(0, 0), close_time=dt_time(23, 59), enabled=True)
        )
    db_session.add(
        PrintServiceControl(manual_override="AUTO", effective_status="RUNNING", reason="test", updated_by="test")
    )

    user = User(whatsapp_user_id="wa_unk_001", phone_number="+919000000003")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        order_code="ORD-UNK-001",
        user_id=user.id,
        status="PAID",
        total_pages=1,
        pickup_token="T003",
    )
    db_session.add(order)
    await db_session.flush()

    order_file = OrderFile(
        order_id=order.id,
        upload_sequence=1,
        original_filename="unk.pdf",
        file_hash="unk_hash",
        storage_path="/storage/private/unk.pdf",
        page_count=1,
        file_size=3000,
        status="VALIDATED",
    )
    db_session.add(order_file)
    await db_session.flush()

    db_session.add(
        PrintConfiguration(
            order_id=order.id, file_id=order_file.id, color_mode="bw", default_sides="single", rules_json="[]"
        )
    )
    await db_session.flush()

    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    claim = await client.post("/agent/claim-job", headers=AGENT_HEADERS, json={"agent_id": "agent-local-01"})
    job_id = claim.json()["job_id"]

    res = await client.post(
        f"/agent/jobs/{job_id}/status",
        headers=AGENT_HEADERS,
        json={"agent_id": "agent-local-01", "status": "UNKNOWN", "error_code": "NETWORK_TIMEOUT"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "FAILED"
    assert res.json()["order_status"] == "PRINT_RESULT_UNKNOWN"


@pytest.mark.asyncio
async def test_agent_job_status_update_nonexistent_job(client):
    """Status update for nonexistent job_id returns 404."""
    res = await client.post(
        "/agent/jobs/nonexistent-id-9999/status",
        headers=AGENT_HEADERS,
        json={"agent_id": "agent-local-01", "status": "SUCCESS"},
    )
    assert res.status_code == 404


# ─── PrinterDriver mock connectivity ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_printer_driver_executes_async_sleep():
    """Confirm print_file awaits sleep (proves it's truly async)."""
    driver = PrinterDriver()
    sleep_called = []

    async def fake_sleep(t):
        sleep_called.append(t)

    with patch("print_agent.printer.asyncio.sleep", fake_sleep):
        with patch("print_agent.printer.agent_settings") as cfg:
            cfg.MOCK_PRINT_EXECUTION_TIME_SECONDS = 3
            await driver.print_file(
                file_path="/f.pdf", page_count=1, color_mode="bw", default_sides="single", rules_json="[]"
            )

    assert sleep_called == [3]


# ─── PrintAgentDaemon unit (mocked HTTP) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_daemon_send_heartbeat_logs_pending_count():
    """Daemon sends heartbeat and logs pending_queue_count from response."""
    from print_agent.agent import PrintAgentDaemon

    daemon = PrintAgentDaemon()
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"acknowledged": True, "server_time": "2026-09-25T00:00:00Z", "pending_queue_count": 3}
    mock_client.post.return_value = mock_response

    await daemon.send_heartbeat(mock_client)

    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert "heartbeat" in call_kwargs[0][0]
    sent_json = call_kwargs[1]["json"]
    assert sent_json["agent_id"] == daemon.agent_id
    assert sent_json["status"] == "ONLINE"
    assert sent_json["printer_status"] == "READY"


@pytest.mark.asyncio
async def test_daemon_heartbeat_tolerates_connection_error():
    """Daemon heartbeat catches connection errors without crashing."""
    from print_agent.agent import PrintAgentDaemon

    daemon = PrintAgentDaemon()
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("Connection refused")

    # Should not raise
    await daemon.send_heartbeat(mock_client)


@pytest.mark.asyncio
async def test_daemon_poll_no_job_found():
    """poll_and_process_job returns early when job_found=False."""
    from print_agent.agent import PrintAgentDaemon

    daemon = PrintAgentDaemon()
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"job_found": False}
    mock_client.post.return_value = mock_response

    await daemon.poll_and_process_job(mock_client)

    # Only one call: claim-job; no printer_driver call
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_daemon_poll_job_found_success():
    """poll_and_process_job calls print_file and posts SUCCESS status."""
    from print_agent.agent import PrintAgentDaemon

    daemon = PrintAgentDaemon()
    mock_client = AsyncMock()

    claim_response = MagicMock()
    claim_response.status_code = 200
    claim_response.json.return_value = {
        "job_found": True,
        "job_id": "job-abc-123",
        "order_code": "ORD-1000",
        "pickup_token": "X999",
        "files": [
            {
                "storage_path": "/storage/private/doc.pdf",
                "page_count": 3,
                "color_mode": "bw",
                "default_sides": "single",
                "rules_json": "[]",
            }
        ],
    }

    status_response = MagicMock()
    status_response.status_code = 200

    mock_client.post.side_effect = [claim_response, status_response]

    with patch("print_agent.agent.printer_driver") as mock_driver:
        mock_driver.print_file = AsyncMock(return_value=True)
        await daemon.poll_and_process_job(mock_client)

    assert mock_client.post.call_count == 2
    # Second call must be job status update with SUCCESS
    status_call = mock_client.post.call_args_list[1]
    assert "job-abc-123" in status_call[0][0]
    assert status_call[1]["json"]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_daemon_poll_job_found_print_fails():
    """poll_and_process_job posts FAILED when printer_driver returns False."""
    from print_agent.agent import PrintAgentDaemon

    daemon = PrintAgentDaemon()
    mock_client = AsyncMock()

    claim_response = MagicMock()
    claim_response.status_code = 200
    claim_response.json.return_value = {
        "job_found": True,
        "job_id": "job-fail-456",
        "order_code": "ORD-1001",
        "pickup_token": "Y888",
        "files": [
            {
                "storage_path": "/storage/private/broken.pdf",
                "page_count": 2,
                "color_mode": "bw",
                "default_sides": "single",
                "rules_json": "[]",
            }
        ],
    }

    status_response = MagicMock()
    status_response.status_code = 200
    mock_client.post.side_effect = [claim_response, status_response]

    with patch("print_agent.agent.printer_driver") as mock_driver:
        mock_driver.print_file = AsyncMock(return_value=False)
        await daemon.poll_and_process_job(mock_client)

    status_call = mock_client.post.call_args_list[1]
    assert status_call[1]["json"]["status"] == "FAILED"
    assert status_call[1]["json"]["error_code"] == "PRINT_EXECUTION_ERROR"


@pytest.mark.asyncio
async def test_daemon_poll_tolerates_non_200_claim():
    """poll_and_process_job ignores non-200 response from claim-job."""
    from print_agent.agent import PrintAgentDaemon

    daemon = PrintAgentDaemon()
    mock_client = AsyncMock()

    response = MagicMock()
    response.status_code = 503
    mock_client.post.return_value = response

    # Should not raise, should not call printer
    await daemon.poll_and_process_job(mock_client)
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_daemon_poll_handles_exception():
    """poll_and_process_job catches unexpected exceptions."""
    from print_agent.agent import PrintAgentDaemon

    daemon = PrintAgentDaemon()
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("Timeout")

    await daemon.poll_and_process_job(mock_client)  # must not raise
