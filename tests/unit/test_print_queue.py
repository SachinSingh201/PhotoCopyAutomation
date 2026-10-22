"""
Tests: Print Queue service (backend/services/queue/service.py)

Covers:
  - calculate_job_duration_seconds math
  - enqueue_verified_order: creates PrintJob + QueueEntry, increments position
  - enqueue_verified_order: rejects wrong order status
  - claim_next_job: respects shop-open gate (PAUSED = no claim)
  - claim_next_job: priority ordering (higher priority claimed first)
  - claim_next_job: held jobs skipped
  - claim_next_job: creates PrintAttempt, transitions order to PRINTING
  - update_job_status SUCCESS: job=PRINTED, order=READY_FOR_PICKUP, queue=DEQUEUED
  - update_job_status FAILED (1st attempt): order=PRINT_FAILED, not yet MANUAL_REQUIRED
  - update_job_status FAILED (2nd attempt): order=MANUAL_REQUIRED
  - update_job_status UNKNOWN: order=PRINT_RESULT_UNKNOWN
  - hold_job / resume_job: toggle is_held + queue entry status
  - retry_job: resets FAILED job back to QUEUED
  - manual_print_job: directly marks PRINTED + READY_FOR_PICKUP
  - calculate_order_eta: sums ahead durations correctly
  - GET /queue: returns only WAITING/PROCESSING entries in position order
"""

import pytest
from datetime import datetime, timezone, time as dt_time

from backend.models.user import User
from backend.models.order import Order
from backend.models.file import OrderFile
from backend.models.configuration import PrintConfiguration
from backend.models.printer import Printer
from backend.models.queue import PrintJob, QueueEntry, PrintAttempt
from backend.models.schedule import PrintServiceControl, PrintingSchedule
from backend.services.queue.service import (
    calculate_job_duration_seconds,
    enqueue_verified_order,
    claim_next_job,
    update_job_status,
    hold_job,
    resume_job,
    retry_job,
    manual_print_job,
    calculate_order_eta,
)
from backend.core.exceptions import AppException


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _seed_open_schedule(session):
    """Seed schedule so shop is always open during tests."""
    for day in range(7):
        session.add(
            PrintingSchedule(
                day_of_week=day,
                open_time=dt_time(0, 0),
                close_time=dt_time(23, 59),
                enabled=True,
            )
        )
    session.add(
        PrintServiceControl(
            manual_override="AUTO",
            effective_status="RUNNING",
            reason="test",
            updated_by="test",
        )
    )
    await session.flush()


async def _create_ready_order(session, order_code: str, phone_suffix: str, pages: int = 4, pickup_token: str = None, color_mode: str = "bw") -> Order:
    """Create a fully configured PAID order ready for queuing."""
    user = User(whatsapp_user_id=f"wa_{phone_suffix}", phone_number=f"+91{phone_suffix}")
    session.add(user)
    await session.flush()

    # Use order_code as pickup token base to guarantee uniqueness
    token = pickup_token or f"T{order_code.replace('-', '')[-6:]}"
    order = Order(
        order_code=order_code,
        user_id=user.id,
        status="PAID",
        total_pages=pages,
        pickup_token=token,
    )
    session.add(order)
    await session.flush()

    order_file = OrderFile(
        order_id=order.id,
        upload_sequence=1,
        original_filename="doc.pdf",
        file_hash=f"hash_{phone_suffix}",
        storage_path=f"/storage/private/{order_code}.pdf",
        page_count=pages,
        file_size=10000,
        status="VALIDATED",
    )
    session.add(order_file)
    await session.flush()

    session.add(
        PrintConfiguration(
            order_id=order.id,
            file_id=order_file.id,
            color_mode=color_mode,
            default_sides="single",
            rules_json="[]",
        )
    )
    await session.flush()
    return order


# ─── calculate_job_duration_seconds ───────────────────────────────────────────

def test_calculate_duration_basic():
    """10 pages at 20ppm = 30s print + 10s warmup = 40s."""
    assert calculate_job_duration_seconds(10, 20) == 40


def test_calculate_duration_zero_pages():
    """0 pages: warmup only = 10s."""
    assert calculate_job_duration_seconds(0, 20) == 10


def test_calculate_duration_uses_ceil():
    """5 pages at 20ppm = ceil(15s) + 10 = 25s."""
    assert calculate_job_duration_seconds(5, 20) == 25


def test_calculate_duration_zero_ppm_guard():
    """ppm=0 guarded by max(ppm,1): should not divide by zero."""
    result = calculate_job_duration_seconds(10, 0)
    assert result > 0


def test_calculate_duration_large_job():
    """200 pages at 20ppm = ceil(600s) + 10 = 610s."""
    assert calculate_job_duration_seconds(200, 20) == 610


# ─── enqueue_verified_order ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enqueue_creates_print_job_and_queue_entry(db_session):
    order = await _create_ready_order(db_session, "ORD-Q001", "9000000010")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    assert job.status == "QUEUED"
    assert job.queue_position == 1
    assert job.is_held is False
    assert job.estimated_duration_seconds > 0
    assert order.status == "QUEUED"
    await db_session.refresh(job, ["queue_entry"])
    assert job.queue_entry is not None
    assert job.queue_entry.status == "WAITING"
    assert job.queue_entry.position == 1


@pytest.mark.asyncio
async def test_enqueue_positions_increment(db_session):
    """Second enqueue gets position 2."""
    o1 = await _create_ready_order(db_session, "ORD-Q002", "9000000020")
    o2 = await _create_ready_order(db_session, "ORD-Q003", "9000000021")

    j1 = await enqueue_verified_order(db_session, o1)
    j2 = await enqueue_verified_order(db_session, o2)
    await db_session.flush()

    assert j1.queue_position == 1
    assert j2.queue_position == 2


@pytest.mark.asyncio
async def test_enqueue_rejects_non_paid_order(db_session):
    """Order not in PAID/WAITING_FOR_PRINT_WINDOW raises AppException."""
    user = User(whatsapp_user_id="wa_bad", phone_number="+910000000099")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        order_code="ORD-BAD-001",
        user_id=user.id,
        status="CREATED",
        total_pages=2,
        pickup_token="TBAD",
    )
    db_session.add(order)
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await enqueue_verified_order(db_session, order)
    assert "INVALID_STATE_TRANSITION" in exc.value.code.value or "INVALID" in str(exc.value.code)


@pytest.mark.asyncio
async def test_enqueue_waiting_for_print_window_order(db_session):
    """WAITING_FOR_PRINT_WINDOW order can be enqueued."""
    user = User(whatsapp_user_id="wa_wpw", phone_number="+910000000088")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        order_code="ORD-WPW-001",
        user_id=user.id,
        status="WAITING_FOR_PRINT_WINDOW",
        total_pages=3,
        pickup_token="TWPW",
    )
    db_session.add(order)
    await db_session.flush()
    order_file = OrderFile(
        order_id=order.id, upload_sequence=1, original_filename="f.pdf",
        file_hash="h1", storage_path="/s/f.pdf", page_count=3, file_size=1000, status="VALIDATED",
    )
    db_session.add(order_file)
    await db_session.flush()
    db_session.add(PrintConfiguration(order_id=order.id, file_id=order_file.id, color_mode="bw", default_sides="single", rules_json="[]"))
    await db_session.flush()

    job = await enqueue_verified_order(db_session, order)
    assert job.status == "QUEUED"


# ─── claim_next_job ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claim_job_empty_queue(db_session):
    await _seed_open_schedule(db_session)
    result = await claim_next_job(db_session, "agent-local-01")
    assert result.job_found is False


@pytest.mark.asyncio
async def test_claim_job_when_paused(db_session):
    """Paused print service: claim returns job_found=False."""
    db_session.add(
        PrintServiceControl(manual_override="PAUSED", effective_status="PAUSED", reason="test", updated_by="test")
    )
    await db_session.flush()

    order = await _create_ready_order(db_session, "ORD-PAUSED-01", "9000000030")
    await enqueue_verified_order(db_session, order)
    await db_session.flush()

    result = await claim_next_job(db_session, "agent-local-01")
    assert result.job_found is False


@pytest.mark.asyncio
async def test_claim_job_skips_held_job(db_session):
    """Held job is not claimed."""
    await _seed_open_schedule(db_session)

    order = await _create_ready_order(db_session, "ORD-HELD-01", "9000000040")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    await hold_job(db_session, job.id, reason="Admin hold")
    await db_session.flush()

    result = await claim_next_job(db_session, "agent-local-01")
    assert result.job_found is False


@pytest.mark.asyncio
async def test_claim_job_transitions_to_printing(db_session):
    """Successful claim transitions job→PRINTING, order→PRINTING, creates attempt."""
    await _seed_open_schedule(db_session)

    order = await _create_ready_order(db_session, "ORD-CLM-01", "9000000050", pickup_token="T050")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    result = await claim_next_job(db_session, "agent-local-01")
    assert result.job_found is True
    assert result.job_id == job.id
    assert result.order_code == "ORD-CLM-01"
    assert result.pickup_token == "T050"
    assert len(result.files) == 1

    # DB state — refresh lazy relationships
    await db_session.refresh(job, ["queue_entry", "attempts"])
    assert job.status == "PRINTING"
    assert job.started_at is not None
    assert job.queue_entry.status == "PROCESSING"
    assert len(job.attempts) == 1
    assert job.attempts[0].agent_id == "agent-local-01"
    assert job.attempts[0].mode == "AUTO"
    await db_session.refresh(order)
    assert order.status == "PRINTING"


@pytest.mark.asyncio
async def test_claim_job_priority_ordering(db_session):
    """Higher priority job claimed first regardless of position."""
    await _seed_open_schedule(db_session)

    low_order = await _create_ready_order(db_session, "ORD-PRI-LOW", "9000000060", pickup_token="PL060")
    high_order = await _create_ready_order(db_session, "ORD-PRI-HIGH", "9000000061", pickup_token="PH061")

    low_job = await enqueue_verified_order(db_session, low_order)
    high_job = await enqueue_verified_order(db_session, high_order)
    # Boost high_job priority after enqueue
    high_job.priority = 10
    await db_session.flush()

    result = await claim_next_job(db_session, "agent-local-01")
    assert result.job_id == high_job.id


@pytest.mark.asyncio
async def test_claim_job_second_claim_empty(db_session):
    """After one job is claimed, second claim finds nothing."""
    await _seed_open_schedule(db_session)

    order = await _create_ready_order(db_session, "ORD-SEC-01", "9000000070")
    await enqueue_verified_order(db_session, order)
    await db_session.flush()

    r1 = await claim_next_job(db_session, "agent-local-01")
    assert r1.job_found is True

    r2 = await claim_next_job(db_session, "agent-local-01")
    assert r2.job_found is False


# ─── update_job_status ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_job_status_success(db_session):
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-SUC-01", "9000000080", pickup_token="T080")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()
    await claim_next_job(db_session, "agent-local-01")
    await db_session.flush()

    updated = await update_job_status(db_session, job.id, "agent-local-01", "SUCCESS")
    await db_session.flush()

    assert updated.status == "PRINTED"
    assert updated.completed_at is not None
    assert updated.queue_entry.status == "DEQUEUED"
    await db_session.refresh(order)
    assert order.status == "READY_FOR_PICKUP"


@pytest.mark.asyncio
async def test_update_job_status_first_failure(db_session):
    """First FAILED attempt: order goes to PRINT_FAILED, not MANUAL_REQUIRED."""
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-F1-01", "9000000090")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()
    await claim_next_job(db_session, "agent-local-01")
    await db_session.flush()

    updated = await update_job_status(db_session, job.id, "agent-local-01", "FAILED", "JAM")
    await db_session.flush()

    assert updated.status == "FAILED"
    await db_session.refresh(order)
    assert order.status == "PRINT_FAILED"


@pytest.mark.asyncio
async def test_update_job_status_second_failure_manual_required(db_session):
    """Two FAILED attempts exhaust retries → MANUAL_REQUIRED."""
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-F2-01", "9000000091")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    # First attempt
    await claim_next_job(db_session, "agent-local-01")
    await db_session.flush()
    await update_job_status(db_session, job.id, "agent-local-01", "FAILED", "ERR1")
    await db_session.flush()

    # Retry — commit so next query sees clean DB state
    await retry_job(db_session, job.id)
    await db_session.commit()

    # Second attempt — commit after claim so status update sees PRINTING order
    await claim_next_job(db_session, "agent-local-01")
    await db_session.commit()
    updated = await update_job_status(db_session, job.id, "agent-local-01", "FAILED", "ERR2")

    assert updated.status == "FAILED"
    await db_session.refresh(order)
    assert order.status == "MANUAL_REQUIRED"


@pytest.mark.asyncio
async def test_update_job_status_unknown(db_session):
    """UNKNOWN status → PRINT_RESULT_UNKNOWN on order."""
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-UNK-01", "9000000092")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()
    await claim_next_job(db_session, "agent-local-01")
    await db_session.flush()

    await update_job_status(db_session, job.id, "agent-local-01", "UNKNOWN", "NETWORK_FAIL")
    await db_session.flush()

    await db_session.refresh(order)
    assert order.status == "PRINT_RESULT_UNKNOWN"


@pytest.mark.asyncio
async def test_update_job_status_nonexistent_raises(db_session):
    """Updating status for unknown job_id raises AppException."""
    with pytest.raises(AppException):
        await update_job_status(db_session, "nonexistent-id", "agent-local-01", "SUCCESS")


# ─── hold_job / resume_job ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hold_and_resume_job(db_session):
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-HOLD-01", "9000000100")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    held = await hold_job(db_session, job.id, reason="Paper jam manual hold")
    await db_session.flush()
    assert held.is_held is True
    assert held.held_reason == "Paper jam manual hold"
    assert held.queue_entry.status == "HELD"

    resumed = await resume_job(db_session, job.id)
    await db_session.flush()
    assert resumed.is_held is False
    assert resumed.held_reason is None
    assert resumed.queue_entry.status == "WAITING"


@pytest.mark.asyncio
async def test_hold_nonexistent_job_raises(db_session):
    with pytest.raises(AppException):
        await hold_job(db_session, "bad-id")


# ─── retry_job ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_job_resets_to_queued(db_session):
    """retry_job resets a FAILED job back to QUEUED."""
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-RETRY-01", "9000000110")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()
    await claim_next_job(db_session, "agent-local-01")
    await db_session.flush()
    await update_job_status(db_session, job.id, "agent-local-01", "FAILED", "ERR")
    await db_session.flush()

    assert job.status == "FAILED"

    retried = await retry_job(db_session, job.id)
    await db_session.flush()
    assert retried.status == "QUEUED"
    assert retried.is_held is False
    assert retried.queue_entry.status == "WAITING"


@pytest.mark.asyncio
async def test_retry_job_nonexistent_raises(db_session):
    with pytest.raises(AppException):
        await retry_job(db_session, "nonexistent-id")


# ─── manual_print_job ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_print_job_from_queued(db_session):
    """Admin manually prints a QUEUED job → READY_FOR_PICKUP."""
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-MAN-01", "9000000120", pickup_token="M120")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    result = await manual_print_job(db_session, job.id, actor_id="admin_user")
    await db_session.flush()

    assert result.status == "PRINTED"
    assert result.completed_at is not None
    await db_session.refresh(result, ["queue_entry", "attempts"])
    assert result.queue_entry.status == "DEQUEUED"
    await db_session.refresh(order)
    assert order.status == "READY_FOR_PICKUP"
    assert len(result.attempts) == 1
    assert result.attempts[0].mode == "MANUAL"
    assert result.attempts[0].status == "SUCCESS"


@pytest.mark.asyncio
async def test_manual_print_job_from_failed(db_session):
    """Admin manual print from PRINT_FAILED → READY_FOR_PICKUP."""
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-MAN-02", "9000000121", pickup_token="M121")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()
    await claim_next_job(db_session, "agent-local-01")
    await db_session.flush()
    await update_job_status(db_session, job.id, "agent-local-01", "FAILED", "ERR")
    await db_session.flush()

    result = await manual_print_job(db_session, job.id, actor_id="admin_user")
    await db_session.flush()

    assert result.status == "PRINTED"
    await db_session.refresh(order)
    assert order.status == "READY_FOR_PICKUP"


@pytest.mark.asyncio
async def test_manual_print_nonexistent_raises(db_session):
    with pytest.raises(AppException):
        await manual_print_job(db_session, "bad-id")


# ─── calculate_order_eta ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calculate_order_eta_with_no_ahead(db_session):
    """ETA with nothing ahead = job's own estimated_duration."""
    await _seed_open_schedule(db_session)
    order = await _create_ready_order(db_session, "ORD-ETA-01", "9000000130")
    job = await enqueue_verified_order(db_session, order)
    await db_session.flush()

    eta = await calculate_order_eta(db_session, job)
    assert eta == job.estimated_duration_seconds


@pytest.mark.asyncio
async def test_calculate_order_eta_with_jobs_ahead(db_session):
    """ETA = sum of WAITING jobs at lower positions + this job."""
    await _seed_open_schedule(db_session)
    o1 = await _create_ready_order(db_session, "ORD-ETA-02", "9000000131")
    o2 = await _create_ready_order(db_session, "ORD-ETA-03", "9000000132")
    j1 = await enqueue_verified_order(db_session, o1)
    j2 = await enqueue_verified_order(db_session, o2)
    await db_session.flush()

    eta = await calculate_order_eta(db_session, j2)
    assert eta == j1.estimated_duration_seconds + j2.estimated_duration_seconds


# ─── GET /queue API endpoint ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_api_empty(client):
    """GET /queue returns empty list when nothing queued."""
    res = await client.get("/queue")
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_queue_api_shows_waiting_jobs(client, db_session):
    """GET /queue returns jobs in position order."""
    await _seed_open_schedule(db_session)
    o1 = await _create_ready_order(db_session, "ORD-QAPI-01", "9000000140", pickup_token="PA001")
    o2 = await _create_ready_order(db_session, "ORD-QAPI-02", "9000000141", pickup_token="PA002")
    await enqueue_verified_order(db_session, o1)
    await enqueue_verified_order(db_session, o2)
    await db_session.commit()

    res = await client.get("/queue")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    assert data[0]["order_code"] == "ORD-QAPI-01"
    assert data[1]["order_code"] == "ORD-QAPI-02"
    assert data[0]["queue_position"] == 1
    assert data[1]["queue_position"] == 2


@pytest.mark.asyncio
async def test_queue_api_excludes_dequeued(client, db_session):
    """GET /queue excludes DEQUEUED entries."""
    await _seed_open_schedule(db_session)
    o1 = await _create_ready_order(db_session, "ORD-QAPI-03", "9000000150", pickup_token="M150")
    j1 = await enqueue_verified_order(db_session, o1)
    await db_session.flush()

    # Mark as done using service (triggers proper state change)
    await claim_next_job(db_session, "agent-local-01")
    await db_session.flush()
    await update_job_status(db_session, j1.id, "agent-local-01", "SUCCESS")
    await db_session.commit()

    res = await client.get("/queue")
    assert res.status_code == 200
    assert res.json() == []


# ─── Multi-Printer Concurrent & Whole Order Tests ─────────────────────────────

@pytest.mark.asyncio
async def test_multi_printer_concurrent_claims_no_order_splitting(db_session):
    """
    Tests that:
    1. Multiple printers (Printer A and Printer B) can claim separate orders concurrently.
    2. Each order (with all its files) is assigned completely to one printer.
    3. An order is never split across printers.
    4. A printer already busy with an order cannot take another order until finished.
    """
    await _seed_open_schedule(db_session)

    # 1. Create two distinct printers: Printer A (BW) and Printer B (Color)
    p_bw = Printer(
        name="Counter Printer BW",
        model="Brother L2320D",
        connection_type="USB",
        status="connected",
        is_default=True,
        supports_color=False,
        supports_duplex=True,
    )
    p_color = Printer(
        name="Back Office Color",
        model="Epson L3250 Color",
        connection_type="Network",
        status="connected",
        is_default=False,
        supports_color=True,
        supports_duplex=False,
    )
    db_session.add(p_bw)
    db_session.add(p_color)
    await db_session.flush()

    # 2. Create Order 1 with 3 files (B&W)
    o1 = await _create_ready_order(db_session, "ORD-MULTI-01", "9000000201", pickup_token="T201")
    # Add 2 more files to Order 1 (total 3 files)
    f2 = OrderFile(order_id=o1.id, upload_sequence=2, original_filename="doc2.pdf", file_hash="hash_doc2", storage_path="/storage/doc2.pdf", file_size=1000, mime_type="application/pdf", page_count=5, status="CONFIRMED")
    f3 = OrderFile(order_id=o1.id, upload_sequence=3, original_filename="doc3.pdf", file_hash="hash_doc3", storage_path="/storage/doc3.pdf", file_size=1000, mime_type="application/pdf", page_count=8, status="CONFIRMED")
    db_session.add_all([f2, f3])
    await db_session.flush()
    db_session.add(PrintConfiguration(order_id=o1.id, file_id=f2.id, color_mode="bw", default_sides="single", rules_json="[]"))
    db_session.add(PrintConfiguration(order_id=o1.id, file_id=f3.id, color_mode="bw", default_sides="single", rules_json="[]"))
    o1.total_pages = 10 + 5 + 8
    await db_session.flush()

    # Create Order 2 with 2 files (Color)
    o2 = await _create_ready_order(db_session, "ORD-MULTI-02", "9000000202", pickup_token="T202", color_mode="color")
    f2_color = OrderFile(order_id=o2.id, upload_sequence=2, original_filename="poster.pdf", file_hash="hash_poster", storage_path="/storage/poster.pdf", file_size=2000, mime_type="application/pdf", page_count=4, status="CONFIRMED")
    db_session.add(f2_color)
    await db_session.flush()
    db_session.add(PrintConfiguration(order_id=o2.id, file_id=f2_color.id, color_mode="color", default_sides="single", rules_json="[]"))
    o2.total_pages = 10 + 4
    await db_session.flush()

    # 3. Enqueue both orders
    j1 = await enqueue_verified_order(db_session, o1)
    j2 = await enqueue_verified_order(db_session, o2)
    await db_session.flush()

    # 4. Printer A (BW) claims next job
    claim1 = await claim_next_job(db_session, agent_id="agent-01", printer_id=p_bw.id)
    assert claim1.job_found is True
    assert claim1.job_id == j1.id
    assert claim1.order_code == "ORD-MULTI-01"
    assert claim1.printer_id == p_bw.id
    # Ensure ALL 3 files of Order 1 are present on this single printer claim (no order splitting!)
    assert len(claim1.files) == 3

    # 5. Printer A attempts to claim another job while currently printing -> must be rejected (1 order per printer)
    claim_busy = await claim_next_job(db_session, agent_id="agent-01", printer_id=p_bw.id)
    assert claim_busy.job_found is False

    # 6. Printer B (Color) concurrently claims a job -> gets Order 2
    claim2 = await claim_next_job(db_session, agent_id="agent-01", printer_id=p_color.id)
    assert claim2.job_found is True
    assert claim2.job_id == j2.id
    assert claim2.order_code == "ORD-MULTI-02"
    assert claim2.printer_id == p_color.id
    # Ensure ALL 2 files of Order 2 are present on this printer claim
    assert len(claim2.files) == 2

    # 7. Complete Order 1 on Printer A
    await update_job_status(db_session, j1.id, agent_id="agent-01", status="SUCCESS")
    await db_session.flush()

    # Now Printer A is idle again
    busy_check = await claim_next_job(db_session, agent_id="agent-01", printer_id=p_bw.id)
    # No more waiting jobs
    assert busy_check.job_found is False
