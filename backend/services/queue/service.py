import json
import math
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.core.config import settings
from backend.core.exceptions import AppException, ErrorCode
from backend.core.logging import logger
from backend.models.audit import AuditLog
from backend.models.configuration import PrintConfiguration
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.models.printer import Printer
from backend.models.queue import PrintAttempt, PrintJob, QueueEntry
from backend.schemas.agent import JobClaimResponse, PrintJobFileDetail
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.scheduling.service import get_effective_print_state


def calculate_job_duration_seconds(total_pages: int, ppm: int = 20) -> int:
    """Calculate estimated print duration in seconds (warmup + pages / ppm * 60)."""
    warmup_seconds = 10
    print_seconds = math.ceil((total_pages / max(ppm, 1)) * 60)
    return warmup_seconds + print_seconds


async def calculate_order_eta(session: AsyncSession, job: PrintJob) -> int:
    """Calculate ETA in seconds: remaining current jobs ahead + this job."""
    query = (
        select(func.sum(PrintJob.estimated_duration_seconds))
        .join(QueueEntry, QueueEntry.print_job_id == PrintJob.id)
        .where(
            QueueEntry.position < job.queue_position,
            QueueEntry.status.in_(["WAITING", "PROCESSING"]),
        )
    )
    res = await session.execute(query)
    ahead_duration = res.scalar_one() or 0
    return ahead_duration + job.estimated_duration_seconds


async def enqueue_verified_order(session: AsyncSession, order: Order) -> PrintJob:
    """
    Creates PrintJob and QueueEntry for a paid order.
    Transitions order to QUEUED.
    """
    if order.status not in [OrderState.PAID.value, OrderState.WAITING_FOR_PRINT_WINDOW.value]:
        raise AppException(
            code=ErrorCode.INVALID_STATE_TRANSITION,
            message=f"Cannot enqueue order from status {order.status}",
        )

    # Determine next queue position
    pos_query = select(func.coalesce(func.max(QueueEntry.position), 0))
    pos_res = await session.execute(pos_query)
    next_position = (pos_res.scalar_one() or 0) + 1

    duration = calculate_job_duration_seconds(order.total_pages, settings.PRINTER_SPEED_PPM)
    now = datetime.now(timezone.utc)

    job = PrintJob(
        order_id=order.id,
        status="QUEUED",
        queue_position=next_position,
        estimated_duration_seconds=duration,
        is_held=False,
    )
    session.add(job)
    await session.flush()

    queue_entry = QueueEntry(
        print_job_id=job.id,
        position=next_position,
        status="WAITING",
        enqueued_at=now,
    )
    session.add(queue_entry)

    # Transition order state to QUEUED
    await transition_order(
        session=session,
        order=order,
        target_state=OrderState.QUEUED,
        actor_type="BACKEND",
        metadata={"job_id": job.id, "queue_position": next_position},
    )

    await session.flush()
    return job


async def claim_next_job(
    session: AsyncSession,
    agent_id: str,
    printer_id: Optional[str] = None,
    printer_name: Optional[str] = None,
) -> JobClaimResponse:
    """
    Atomic claim of next eligible waiting print job by local Print Agent for a specific or idle printer.
    Guarantees:
    1. One printer prints only one order at a time.
    2. The order is NEVER split across multiple printers (all files in an order are printed on the same printer).
    3. Multiple printers operate concurrently on separate orders.
    """
    now = datetime.now(timezone.utc)

    # Check effective print service status
    state = await get_effective_print_state(session, now)
    if not state["shop_open"] or state["effective_status"] != "RUNNING":
        logger.info(f"Job claim skipped: print service is {state['effective_status']} ({state['reason']})")
        return JobClaimResponse(job_found=False)

    # 1. Resolve Target Printer
    target_printer = None
    if printer_id:
        p_res = await session.execute(select(Printer).where(Printer.id == printer_id))
        target_printer = p_res.scalar_one_or_none()
        if not target_printer or target_printer.status != "connected":
            logger.info(f"Target printer {printer_id} is not connected or found")
            return JobClaimResponse(job_found=False)
    elif printer_name:
        p_res = await session.execute(select(Printer).where(Printer.name == printer_name))
        target_printer = p_res.scalar_one_or_none()

    # 2. If target printer is specified, verify it is NOT already busy printing an active job
    if target_printer:
        busy_q = select(func.count(PrintJob.id)).where(
            PrintJob.status == "PRINTING",
            PrintJob.printer_id == target_printer.id,
        )
        busy_res = await session.execute(busy_q)
        busy_count = busy_res.scalar_one() or 0
        if busy_count > 0:
            logger.info(f"Printer {target_printer.name} ({target_printer.id}) is currently busy with another order")
            return JobClaimResponse(job_found=False)

    # 3. If target printer is NOT specified, query connected and currently idle printers
    idle_printers = []
    connected_printers = []
    if not target_printer:
        all_printers_res = await session.execute(
            select(Printer).where(Printer.status == "connected").order_by(Printer.is_default.desc())
        )
        connected_printers = all_printers_res.scalars().all()

        if connected_printers:
            busy_p_query = select(PrintJob.printer_id).where(
                PrintJob.status == "PRINTING",
                PrintJob.printer_id.isnot(None),
            )
            busy_p_res = await session.execute(busy_p_query)
            busy_printer_ids = set(busy_p_res.scalars().all())

            idle_printers = [p for p in connected_printers if p.id not in busy_printer_ids]
            if not idle_printers:
                logger.info("All connected printers are currently busy")
                return JobClaimResponse(job_found=False)

    # 4. Query waiting print jobs
    query = (
        select(PrintJob)
        .join(QueueEntry, QueueEntry.print_job_id == PrintJob.id)
        .where(
            QueueEntry.status == "WAITING",
            PrintJob.status == "QUEUED",
            PrintJob.is_held == False,
        )
        .order_by(PrintJob.priority.desc(), QueueEntry.position.asc())
        .options(
            selectinload(PrintJob.order).selectinload(Order.files).selectinload(OrderFile.configuration),
            selectinload(PrintJob.queue_entry),
            selectinload(PrintJob.attempts),
        )
    )
    res = await session.execute(query)
    waiting_jobs = res.scalars().all()

    if not waiting_jobs:
        return JobClaimResponse(job_found=False)

    # 5. Match entire order to an available printer
    assigned_job = None
    assigned_printer = target_printer

    if target_printer:
        for job in waiting_jobs:
            order_requires_color = any(
                f.configuration and f.configuration.color_mode == "color"
                for f in job.order.files if f.status != "DELETED"
            )
            if order_requires_color and not target_printer.supports_color:
                continue
            assigned_job = job
            break
    elif idle_printers:
        for job in waiting_jobs:
            order_requires_color = any(
                f.configuration and f.configuration.color_mode == "color"
                for f in job.order.files if f.status != "DELETED"
            )
            for p in idle_printers:
                if order_requires_color and not p.supports_color:
                    continue
                assigned_job = job
                assigned_printer = p
                break
            if assigned_job:
                break
    else:
        # If no printer registry exists, fall back to first waiting job
        assigned_job = waiting_jobs[0]
        assigned_printer = None

    if not assigned_job:
        return JobClaimResponse(job_found=False)

    # 6. Assign the ENTIRE order to this printer
    assigned_job.status = "PRINTING"
    assigned_job.started_at = now
    if assigned_printer:
        assigned_job.printer_id = assigned_printer.id
        assigned_job.printer_name = assigned_printer.name
    elif printer_name:
        assigned_job.printer_name = printer_name

    if assigned_job.queue_entry:
        assigned_job.queue_entry.status = "PROCESSING"

    attempt_number = len(assigned_job.attempts) + 1

    # Create PrintAttempt record
    attempt = PrintAttempt(
        print_job_id=assigned_job.id,
        attempt_number=attempt_number,
        mode="AUTO",
        status="STARTED",
        agent_id=agent_id,
        printer_id=assigned_job.printer_id,
        printer_name=assigned_job.printer_name,
        started_at=now,
    )
    session.add(attempt)

    # Transition order state to PRINTING
    await transition_order(
        session=session,
        order=assigned_job.order,
        target_state=OrderState.PRINTING,
        actor_type="AGENT",
        actor_id=agent_id,
        metadata={
            "job_id": assigned_job.id,
            "attempt_number": attempt_number,
            "printer_id": assigned_job.printer_id,
            "printer_name": assigned_job.printer_name,
        },
    )

    # Build file details list for all files of this order
    file_details = []
    for f in assigned_job.order.files:
        if f.status == "DELETED":
            continue
        config = f.configuration
        file_details.append(
            PrintJobFileDetail(
                file_id=f.id,
                upload_sequence=f.upload_sequence,
                filename=f.original_filename,
                storage_path=f.storage_path,
                page_count=f.page_count,
                color_mode=config.color_mode if config else "bw",
                default_sides=config.default_sides if config else "single",
                rules_json=config.rules_json if config else "[]",
            )
        )

    await session.flush()
    return JobClaimResponse(
        job_found=True,
        job_id=assigned_job.id,
        order_id=assigned_job.order.id,
        order_code=assigned_job.order.order_code,
        pickup_token=assigned_job.order.pickup_token,
        printer_id=assigned_job.printer_id,
        printer_name=assigned_job.printer_name,
        files=file_details,
    )


async def update_job_status(
    session: AsyncSession,
    job_id: str,
    agent_id: str,
    status: str,  # "SUCCESS" | "FAILED" | "UNKNOWN"
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> PrintJob:
    """
    Updates print job after agent completes or fails printing.
    Bounded auto-retries transition to MANUAL_REQUIRED after threshold.
    """
    query = (
        select(PrintJob)
        .where(PrintJob.id == job_id)
        .options(
            selectinload(PrintJob.order),
            selectinload(PrintJob.queue_entry),
            selectinload(PrintJob.attempts),
        )
        .execution_options(populate_existing=True)
    )
    res = await session.execute(query)
    job = res.scalar_one_or_none()
    if not job:
        raise AppException(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"PrintJob {job_id} not found",
            status_code=404,
        )

    now = datetime.now(timezone.utc)

    # Update attempt
    if job.attempts:
        last_attempt = job.attempts[-1]
        last_attempt.status = status
        last_attempt.completed_at = now
        last_attempt.error_code = error_code
        last_attempt.error_message = error_message

    if status == "SUCCESS":
        job.status = "PRINTED"
        job.completed_at = now
        if job.queue_entry:
            job.queue_entry.status = "DEQUEUED"
            job.queue_entry.dequeued_at = now

        # Transition order to PRINTED -> READY_FOR_PICKUP
        await transition_order(
            session=session,
            order=job.order,
            target_state=OrderState.PRINTED,
            actor_type="AGENT",
            actor_id=agent_id,
        )
        await transition_order(
            session=session,
            order=job.order,
            target_state=OrderState.READY_FOR_PICKUP,
            actor_type="BACKEND",
            metadata={"token": job.order.pickup_token},
        )
    elif status == "UNKNOWN":
        job.status = "FAILED"
        job.error_code = error_code or "PRINT_RESULT_UNKNOWN"
        job.error_message = error_message or "Network disconnected during physical printing"
        if job.queue_entry:
            job.queue_entry.status = "DEQUEUED"

        await transition_order(
            session=session,
            order=job.order,
            target_state=OrderState.PRINT_RESULT_UNKNOWN,
            actor_type="AGENT",
            actor_id=agent_id,
            metadata={"error_code": error_code, "error_message": error_message},
        )
    else:
        # FAILED
        job.status = "FAILED"
        job.error_code = error_code
        job.error_message = error_message
        if job.queue_entry:
            job.queue_entry.status = "DEQUEUED"

        # Check attempt count: bounded retries (<= 2 auto attempts)
        total_attempts = len(job.attempts)
        if total_attempts >= 2:
            # Exhausted auto-retries -> MANUAL_REQUIRED
            await transition_order(
                session=session,
                order=job.order,
                target_state=OrderState.PRINT_FAILED,
                actor_type="AGENT",
                actor_id=agent_id,
                metadata={"error_code": error_code, "error_message": error_message},
            )
            await transition_order(
                session=session,
                order=job.order,
                target_state=OrderState.MANUAL_REQUIRED,
                actor_type="BACKEND",
                metadata={"reason": "Auto retries exhausted"},
            )
        else:
            await transition_order(
                session=session,
                order=job.order,
                target_state=OrderState.PRINT_FAILED,
                actor_type="AGENT",
                actor_id=agent_id,
                metadata={"error_code": error_code, "error_message": error_message, "attempt": total_attempts},
            )

    await session.flush()
    return job


async def hold_job(session: AsyncSession, job_id: str, actor_id: str = "ADMIN", reason: Optional[str] = None) -> PrintJob:
    """Hold a queued print job from being claimed."""
    query = (
        select(PrintJob)
        .where(PrintJob.id == job_id)
        .options(selectinload(PrintJob.order), selectinload(PrintJob.queue_entry))
    )
    res = await session.execute(query)
    job = res.scalar_one_or_none()
    if not job:
        raise AppException(code=ErrorCode.INTERNAL_ERROR, message=f"Job {job_id} not found", status_code=404)

    now = datetime.now(timezone.utc)
    job.is_held = True
    job.held_reason = reason or "Held by admin"
    job.held_at = now
    if job.queue_entry:
        job.queue_entry.status = "HELD"

    session.add(
        AuditLog(
            order_id=job.order_id,
            event_type="JOB_HELD",
            actor_type="ADMIN",
            actor_id=actor_id,
            metadata_json=json.dumps({"job_id": job.id, "reason": job.held_reason}),
        )
    )
    await session.flush()
    return job


async def resume_job(session: AsyncSession, job_id: str, actor_id: str = "ADMIN") -> PrintJob:
    """Resume a held print job."""
    query = (
        select(PrintJob)
        .where(PrintJob.id == job_id)
        .options(selectinload(PrintJob.order), selectinload(PrintJob.queue_entry))
    )
    res = await session.execute(query)
    job = res.scalar_one_or_none()
    if not job:
        raise AppException(code=ErrorCode.INTERNAL_ERROR, message=f"Job {job_id} not found", status_code=404)

    job.is_held = False
    job.held_reason = None
    job.held_at = None
    if job.queue_entry:
        job.queue_entry.status = "WAITING"

    session.add(
        AuditLog(
            order_id=job.order_id,
            event_type="JOB_RESUMED",
            actor_type="ADMIN",
            actor_id=actor_id,
            metadata_json=json.dumps({"job_id": job.id}),
        )
    )
    await session.flush()
    return job


async def retry_job(session: AsyncSession, job_id: str, actor_id: str = "ADMIN") -> PrintJob:
    """Admin re-queues a failed or manual-required print job."""
    query = (
        select(PrintJob)
        .where(PrintJob.id == job_id)
        .options(selectinload(PrintJob.order), selectinload(PrintJob.queue_entry), selectinload(PrintJob.attempts))
    )
    res = await session.execute(query)
    job = res.scalar_one_or_none()
    if not job:
        raise AppException(code=ErrorCode.INTERNAL_ERROR, message=f"Job {job_id} not found", status_code=404)

    now = datetime.now(timezone.utc)
    job.status = "QUEUED"
    job.is_held = False

    # Check or re-create queue entry
    if job.queue_entry:
        job.queue_entry.status = "WAITING"
        job.queue_entry.enqueued_at = now
    else:
        pos_query = select(func.coalesce(func.max(QueueEntry.position), 0))
        pos_res = await session.execute(pos_query)
        next_pos = (pos_res.scalar_one() or 0) + 1
        queue_entry = QueueEntry(
            print_job_id=job.id,
            position=next_pos,
            status="WAITING",
            enqueued_at=now,
        )
        session.add(queue_entry)

    # Transition order state back to QUEUED if needed
    if job.order.status in [OrderState.PRINT_FAILED.value, OrderState.MANUAL_REQUIRED.value]:
        await transition_order(
            session=session,
            order=job.order,
            target_state=OrderState.QUEUED,
            actor_type="ADMIN",
            actor_id=actor_id,
            metadata={"job_id": job.id, "action": "RETRY"},
        )

    await session.flush()
    return job


async def manual_print_job(session: AsyncSession, job_id: str, actor_id: str = "ADMIN") -> PrintJob:
    """Shop owner executes print manually directly from the dashboard."""
    query = (
        select(PrintJob)
        .where(PrintJob.id == job_id)
        .options(selectinload(PrintJob.order), selectinload(PrintJob.queue_entry), selectinload(PrintJob.attempts))
    )
    res = await session.execute(query)
    job = res.scalar_one_or_none()
    if not job:
        raise AppException(code=ErrorCode.INTERNAL_ERROR, message=f"Job {job_id} not found", status_code=404)

    now = datetime.now(timezone.utc)
    attempt_num = len(job.attempts) + 1

    # Record manual attempt
    attempt = PrintAttempt(
        print_job_id=job.id,
        attempt_number=attempt_num,
        mode="MANUAL",
        status="SUCCESS",
        agent_id=f"ADMIN:{actor_id}",
        started_at=now,
        completed_at=now,
    )
    session.add(attempt)

    job.status = "PRINTED"
    job.completed_at = now
    if job.queue_entry:
        job.queue_entry.status = "DEQUEUED"
        job.queue_entry.dequeued_at = now

    # Transition order to PRINTED -> READY_FOR_PICKUP
    order = job.order
    if order.status == OrderState.QUEUED.value:
        await transition_order(session, order, OrderState.MANUAL_REQUIRED, actor_type="ADMIN", actor_id=actor_id)
    elif order.status == OrderState.PRINT_FAILED.value:
        await transition_order(session, order, OrderState.MANUAL_REQUIRED, actor_type="ADMIN", actor_id=actor_id)
    elif order.status == OrderState.PRINT_RESULT_UNKNOWN.value:
        await transition_order(session, order, OrderState.MANUAL_REQUIRED, actor_type="ADMIN", actor_id=actor_id)

    await transition_order(session, order, OrderState.PRINTED, actor_type="ADMIN", actor_id=actor_id)
    await transition_order(session, order, OrderState.READY_FOR_PICKUP, actor_type="BACKEND", metadata={"token": order.pickup_token})

    await session.flush()
    return job
