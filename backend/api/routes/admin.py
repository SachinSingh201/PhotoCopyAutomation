import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.api.dependencies import verify_admin_secret
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.logging import logger
from backend.models.agent import AgentHeartbeat
from backend.models.audit import AuditLog
from backend.models.configuration import PrintConfiguration
from backend.models.file import OrderFile
from backend.models.notification import Notification
from backend.models.order import Order
from backend.models.payment import Payment
from backend.models.printer import Printer
from backend.models.queue import PrintAttempt, PrintJob, QueueEntry
from backend.models.schedule import (
    PrintServiceControl,
    PrintingSchedule,
    ScheduleException,
    ShopSetting,
)
from backend.models.user import User
from backend.schemas.admin import (
    AdminDashboardStats,
    AdminQueueItem,
    AlertItem,
    AuditLogRead,
    CurrentPrintJobDetail,
    HoldJobRequest,
    NotificationItem,
    PricingUpdateRequest,
    PrinterItem,
    PrinterPatchRequest,
    PrintingScheduleItem,
    ReportsSummaryResponse,
    ScheduleExceptionCreate,
    ScheduleExceptionRead,
    ScheduleUpdateRequest,
    SearchResultItem,
    ServiceControlRequest,
    ShopSettingsUpdateRequest,
    SystemStatusResponse,
)
from backend.schemas.orders import OrderRead
from backend.services.orders.service import get_order_by_id
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.queue.service import (
    calculate_order_eta,
    hold_job,
    manual_print_job,
    resume_job,
    retry_job,
)
from backend.services.scheduling.service import (
    get_effective_print_state,
    get_next_print_window,
    seed_default_schedule_if_empty,
)

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(verify_admin_secret)])

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


async def seed_default_printers_if_empty(session: AsyncSession):
    """Seed initial printers if table is empty."""
    res = await session.execute(select(func.count(Printer.id)))
    count = res.scalar_one() or 0
    if count == 0:
        session.add(
            Printer(
                name="HP LaserJet Pro MFP M428fdw",
                model="HP LaserJet Pro M428fdw",
                connection_type="USB",
                ip_address=None,
                status="connected",
                is_default=True,
                supports_color=False,
                supports_duplex=True,
                paper_tray_status="Tray 1: A4 (90% full)",
                toner_level=82,
            )
        )
        session.add(
            Printer(
                name="Canon imageCLASS LBP6230dw",
                model="Canon LBP6230dw Series",
                connection_type="Network",
                ip_address="192.168.1.145",
                status="available",
                is_default=False,
                supports_color=False,
                supports_duplex=True,
                paper_tray_status="Tray 1: A4 (60% full)",
                toner_level=45,
            )
        )
        session.add(
            Printer(
                name="Epson EcoTank L3250",
                model="Epson L3250 Series Color",
                connection_type="USB",
                ip_address=None,
                status="driver_missing",
                is_default=False,
                supports_color=True,
                supports_duplex=False,
                paper_tray_status="Tray 1: A4 (Empty)",
                toner_level=95,
            )
        )
        await session.flush()


# 1. System Status
@router.get("/system/status", response_model=SystemStatusResponse)
async def get_system_status(session: AsyncSession = Depends(get_db)):
    """Returns topbar and sidebar status strip metrics."""
    await seed_default_printers_if_empty(session)
    now = datetime.now(timezone.utc)

    # 1. Agent status
    agent_q = await session.execute(
        select(AgentHeartbeat).order_by(AgentHeartbeat.last_seen_at.desc()).limit(1)
    )
    agent = agent_q.scalar_one_or_none()
    agent_status = "OFFLINE"
    if agent:
        diff = (now - agent.last_seen_at).total_seconds()
        if diff <= settings.AGENT_HEARTBEAT_TIMEOUT_SECONDS:
            agent_status = agent.status

    # 2. Printers connected
    printers_q = await session.execute(
        select(func.count(Printer.id)).where(Printer.status == "connected")
    )
    printers_connected = printers_q.scalar_one() or 0

    # 3. Jobs needing review (failed or manual required)
    review_q = await session.execute(
        select(func.count(Order.id)).where(
            Order.status.in_([OrderState.PRINT_FAILED.value, OrderState.MANUAL_REQUIRED.value, OrderState.PRINT_RESULT_UNKNOWN.value])
        )
    )
    jobs_needing_review = review_q.scalar_one() or 0

    # 4. Operating window & status
    eff_state = await get_effective_print_state(session, now)
    next_win = await get_next_print_window(session, now)

    return SystemStatusResponse(
        agent_status=agent_status,
        printers_connected=printers_connected,
        jobs_needing_review=jobs_needing_review,
        effective_print_status=eff_state["effective_status"],
        manual_override=eff_state["manual_override"],
        active_window=eff_state["reason"],
        next_window=next_win.strftime("%H:%M %d %b") if next_win else None,
    )


# 2. Global Search
@router.get("/search", response_model=List[SearchResultItem])
async def global_search(
    q: str = Query(..., min_length=1), session: AsyncSession = Depends(get_db)
):
    """Global search across Order ID, pickup token, customer phone/name, and filenames."""
    results: List[SearchResultItem] = []
    pattern = f"%{q.strip()}%"

    # Search Orders & Customers
    order_query = (
        select(Order)
        .join(User, User.id == Order.user_id)
        .where(
            or_(
                Order.order_code.ilike(pattern),
                Order.pickup_token.ilike(pattern),
                User.phone_number.ilike(pattern),
                User.display_name.ilike(pattern),
            )
        )
        .options(selectinload(Order.user))
        .limit(10)
    )
    res = await session.execute(order_query)
    for ord in res.scalars().all():
        results.append(
            SearchResultItem(
                type="order",
                id=ord.id,
                title=f"{ord.order_code} (Token: {ord.pickup_token or '—'})",
                subtitle=f"{ord.user.display_name or ord.user.phone_number} • ₹{ord.total_amount:.2f} • {ord.status}",
                token=ord.pickup_token,
                status=ord.status,
            )
        )

    # Search Files
    file_query = (
        select(OrderFile)
        .join(Order, Order.id == OrderFile.order_id)
        .where(OrderFile.original_filename.ilike(pattern))
        .options(selectinload(OrderFile.order))
        .limit(10)
    )
    f_res = await session.execute(file_query)
    for f in f_res.scalars().all():
        results.append(
            SearchResultItem(
                type="file",
                id=f.order_id,
                title=f.original_filename,
                subtitle=f"Order {f.order.order_code} • {f.page_count} pages",
                token=f.order.pickup_token,
                status=f.order.status,
            )
        )

    return results


# 3. Notifications & Alerts
@router.get("/notifications", response_model=List[NotificationItem])
async def list_notifications(session: AsyncSession = Depends(get_db)):
    """Fetch notifications and alerts for topbar bell dropdown."""
    res = await session.execute(
        select(Notification).order_by(Notification.created_at.desc()).limit(20)
    )
    notifs = res.scalars().all()
    return [
        NotificationItem(
            id=n.id,
            order_id=n.order_id,
            title=n.title,
            message=n.message,
            type=n.type,
            status=n.status,
            is_read=n.is_read,
            created_at=n.created_at,
        )
        for n in notifs
    ]


@router.patch("/notifications/{id}/read")
async def mark_notification_read(id: str, session: AsyncSession = Depends(get_db)):
    """Mark notification as read in database."""
    res = await session.execute(select(Notification).where(Notification.id == id))
    notif = res.scalar_one_or_none()
    if notif:
        notif.is_read = True
        await session.commit()
    return {"status": "success", "id": id}


@router.get("/alerts", response_model=List[AlertItem])
async def list_open_alerts(session: AsyncSession = Depends(get_db)):
    """Fetches actionable items for the 'Needs Attention' panel."""
    alerts: List[AlertItem] = []

    # 1. Manual Required Orders
    man_orders = await session.execute(
        select(Order)
        .where(Order.status == OrderState.MANUAL_REQUIRED.value)
        .order_by(Order.created_at.desc())
        .limit(5)
    )
    for ord in man_orders.scalars().all():
        alerts.append(
            AlertItem(
                id=f"alert-man-{ord.id}",
                severity="high",
                type="MANUAL_REQUIRED",
                title=f"Manual Print Required: {ord.order_code}",
                description=f"Token {ord.pickup_token or '—'} exhausted auto retries or requested manual printing ({ord.total_pages} pages).",
                order_id=ord.id,
                token=ord.pickup_token,
                created_at=ord.created_at,
            )
        )

    # 2. Failed Print Jobs
    failed_jobs = await session.execute(
        select(PrintJob)
        .join(Order, Order.id == PrintJob.order_id)
        .where(PrintJob.status == "FAILED")
        .options(selectinload(PrintJob.order))
        .limit(5)
    )
    for j in failed_jobs.scalars().all():
        alerts.append(
            AlertItem(
                id=f"alert-fail-{j.id}",
                severity="high",
                type="PRINT_FAILED",
                title=f"Job Failed: {j.order.order_code}",
                description=f"Error: {j.error_message or 'Physical spooler rejected job'}",
                order_id=j.order_id,
                job_id=j.id,
                token=j.order.pickup_token,
                created_at=j.created_at,
            )
        )

    return alerts


# 4. Currently Printing Widget
@router.get("/print-jobs/current", response_model=CurrentPrintJobDetail)
async def get_current_print_job(session: AsyncSession = Depends(get_db)):
    """Fetches real-time detail of currently executing job."""
    res = await session.execute(
        select(PrintJob)
        .where(PrintJob.status == "PRINTING")
        .options(
            selectinload(PrintJob.order).selectinload(Order.files).selectinload(OrderFile.configuration),
            selectinload(PrintJob.attempts),
        )
        .order_by(PrintJob.started_at.desc())
        .limit(1)
    )
    job = res.scalar_one_or_none()
    if not job or not job.order:
        return CurrentPrintJobDetail(job_found=False)

    order = job.order
    first_file = order.files[0] if order.files else None
    cfg = first_file.configuration if first_file else None
    started = job.started_at or datetime.now(timezone.utc)
    est_comp = started + timedelta(seconds=job.estimated_duration_seconds)

    return CurrentPrintJobDetail(
        job_found=True,
        job_id=job.id,
        order_id=order.id,
        order_code=order.order_code,
        pickup_token=order.pickup_token,
        primary_filename=first_file.original_filename if first_file else "Document.pdf",
        file_count=len(order.files),
        total_pages=order.total_pages,
        color_mode=cfg.color_mode if cfg else "bw",
        default_sides=cfg.default_sides if cfg else "single",
        started_at=started,
        estimated_duration_seconds=job.estimated_duration_seconds,
        estimated_completion_at=est_comp,
    )


# 5. Dashboard Summary (KPIs)
@router.get("/dashboard/summary", response_model=AdminDashboardStats)
@router.get("/dashboard", response_model=AdminDashboardStats)
async def get_admin_dashboard(session: AsyncSession = Depends(get_db)):
    """Aggregates all real-time stats for the admin overview dashboard."""
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    # 1. Orders today
    orders_today_q = await session.execute(
        select(func.count(Order.id)).where(Order.created_at >= today_start)
    )
    orders_today = orders_today_q.scalar_one() or 0

    # 2. Status counts
    waiting_q = await session.execute(
        select(func.count(Order.id)).where(
            Order.status.in_([OrderState.QUEUED.value, OrderState.WAITING_FOR_PRINT_WINDOW.value])
        )
    )
    waiting_count = waiting_q.scalar_one() or 0

    printing_q = await session.execute(
        select(func.count(Order.id)).where(Order.status == OrderState.PRINTING.value)
    )
    printing_count = printing_q.scalar_one() or 0

    ready_q = await session.execute(
        select(func.count(Order.id)).where(Order.status == OrderState.READY_FOR_PICKUP.value)
    )
    ready_count = ready_q.scalar_one() or 0

    collected_q = await session.execute(
        select(func.count(Order.id)).where(
            Order.status == OrderState.COLLECTED.value,
            Order.collected_at >= today_start,
        )
    )
    collected_today = collected_q.scalar_one() or 0

    # 3. Revenue today
    revenue_q = await session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
            Payment.status == "VERIFIED",
            Payment.verified_at >= today_start,
        )
    )
    revenue_today = float(revenue_q.scalar_one() or 0.0)

    # 4. Pages printed today
    pages_q = await session.execute(
        select(func.coalesce(func.sum(Order.total_pages), 0)).where(
            Order.status.in_([OrderState.PRINTED.value, OrderState.READY_FOR_PICKUP.value, OrderState.COLLECTED.value]),
            Order.printed_at >= today_start,
        )
    )
    pages_printed_today = int(pages_q.scalar_one() or 0)

    # 5. Failed and manual jobs
    failed_q = await session.execute(
        select(func.count(Order.id)).where(Order.status == OrderState.PRINT_FAILED.value)
    )
    failed_jobs_count = failed_q.scalar_one() or 0

    manual_q = await session.execute(
        select(func.count(Order.id)).where(Order.status == OrderState.MANUAL_REQUIRED.value)
    )
    manual_jobs_count = manual_q.scalar_one() or 0

    # 6. Agent and printer status
    agent_q = await session.execute(
        select(AgentHeartbeat).order_by(AgentHeartbeat.last_seen_at.desc()).limit(1)
    )
    agent = agent_q.scalar_one_or_none()
    agent_status = "OFFLINE"
    printer_status = "OFFLINE"
    if agent:
        diff = (now - agent.last_seen_at).total_seconds()
        if diff <= settings.AGENT_HEARTBEAT_TIMEOUT_SECONDS:
            agent_status = agent.status
            printer_status = agent.printer_status

    # 7. Print service control state
    effective_state = await get_effective_print_state(session, now)

    return AdminDashboardStats(
        orders_today=orders_today,
        waiting_count=waiting_count,
        printing_count=printing_count,
        ready_count=ready_count,
        collected_today=collected_today,
        revenue_today=revenue_today,
        pages_printed_today=pages_printed_today,
        avg_wait_seconds=45.0,
        failed_jobs_count=failed_jobs_count,
        manual_jobs_count=manual_jobs_count,
        agent_status=agent_status,
        printer_status=printer_status,
        effective_print_status=effective_state["effective_status"],
        manual_override=effective_state["manual_override"],
        currency=settings.CURRENCY,
    )


# 6. Printers Management
@router.get("/printers", response_model=List[PrinterItem])
async def list_printers(session: AsyncSession = Depends(get_db)):
    """List all connected, available, and discovered printers."""
    await seed_default_printers_if_empty(session)
    res = await session.execute(select(Printer).order_by(Printer.is_default.desc(), Printer.name.asc()))
    printers = res.scalars().all()
    return [
        PrinterItem(
            id=p.id,
            name=p.name,
            model=p.model,
            connection_type=p.connection_type,
            ip_address=p.ip_address,
            status=p.status,
            is_default=p.is_default,
            supports_color=p.supports_color,
            supports_duplex=p.supports_duplex,
            paper_tray_status=p.paper_tray_status,
            toner_level=p.toner_level,
            last_test_page_at=p.last_test_page_at,
        )
        for p in printers
    ]


@router.post("/printers/scan")
async def scan_printers(session: AsyncSession = Depends(get_db)):
    """Scan local OS spooler and network for printers."""
    await seed_default_printers_if_empty(session)
    return {"status": "success", "message": "Printer scan completed"}


@router.post("/printers/{id}/connect")
async def connect_printer(id: str, session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(Printer).where(Printer.id == id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Printer not found")
    p.status = "connected"
    await session.commit()
    return {"status": "success", "id": id, "printer_status": p.status}


@router.post("/printers/{id}/disconnect")
async def disconnect_printer(id: str, session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(Printer).where(Printer.id == id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Printer not found")
    p.status = "available"
    if p.is_default:
        p.is_default = False
    await session.commit()
    return {"status": "success", "id": id, "printer_status": p.status}


@router.patch("/printers/{id}")
async def update_printer(id: str, payload: PrinterPatchRequest, session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(Printer).where(Printer.id == id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Printer not found")

    if payload.is_default is not None and payload.is_default:
        # Enforce single default printer
        await session.execute(update(Printer).values(is_default=False))
        p.is_default = True
    elif payload.is_default is False:
        p.is_default = False

    if payload.status:
        p.status = payload.status

    await session.commit()
    return {"status": "success", "id": id, "is_default": p.is_default, "printer_status": p.status}


@router.post("/printers/{id}/test-page")
async def print_test_page(id: str, session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(Printer).where(Printer.id == id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Printer not found")

    now = datetime.now(timezone.utc)
    p.last_test_page_at = now
    session.add(
        AuditLog(
            event_type="TEST_PAGE_PRINTED",
            actor_type="ADMIN",
            actor_id="ADMIN",
            metadata_json=f'{{"printer_id": "{p.id}", "printer_name": "{p.name}"}}',
        )
    )
    await session.commit()
    return {"status": "success", "printer_id": p.id, "message": "Test page sent to printer"}


# 7. Queue Management
@router.get("/queue", response_model=List[AdminQueueItem])
async def list_admin_queue(
    limit: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    """Lists queue jobs with complete details and controls."""
    query = (
        select(PrintJob)
        .join(Order, Order.id == PrintJob.order_id)
        .join(User, User.id == Order.user_id)
        .outerjoin(QueueEntry, QueueEntry.print_job_id == PrintJob.id)
        .where(PrintJob.status.in_(["QUEUED", "PRINTING", "HELD", "FAILED"]))
        .order_by(PrintJob.is_held.asc(), PrintJob.priority.desc(), PrintJob.queue_position.asc())
        .options(
            selectinload(PrintJob.order).selectinload(Order.files),
            selectinload(PrintJob.order).selectinload(Order.payments),
            selectinload(PrintJob.order).selectinload(Order.user),
            selectinload(PrintJob.queue_entry),
        )
    )
    if status_filter:
        query = query.where(PrintJob.status == status_filter)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Order.order_code.ilike(pattern),
                Order.pickup_token.ilike(pattern),
                User.phone_number.ilike(pattern),
                User.display_name.ilike(pattern),
            )
        )
    if limit:
        query = query.limit(limit)

    res = await session.execute(query)
    jobs = res.scalars().all()

    items = []
    for job in jobs:
        order = job.order
        payment_status = "UNPAID"
        if order.payments:
            payment_status = order.payments[-1].status

        eta = await calculate_order_eta(session, job)
        items.append(
            AdminQueueItem(
                job_id=job.id,
                order_id=order.id,
                order_code=order.order_code,
                pickup_token=order.pickup_token,
                customer_name=order.user.display_name,
                customer_phone=order.user.phone_number,
                queue_position=job.queue_position,
                priority=job.priority,
                status=job.status if not job.is_held else "HELD",
                is_held=job.is_held,
                held_reason=job.held_reason,
                total_pages=order.total_pages,
                file_count=len(order.files),
                payment_status=payment_status,
                estimated_duration_seconds=job.estimated_duration_seconds,
                eta_seconds=eta,
                print_mode=order.print_mode,
                enqueued_at=job.queue_entry.enqueued_at if job.queue_entry else job.created_at,
            )
        )
    return items


@router.post("/queue/{job_id}/hold")
async def hold_queue_job(
    job_id: str, payload: Optional[HoldJobRequest] = None, session: AsyncSession = Depends(get_db)
):
    reason = payload.reason if payload else "Admin hold"
    job = await hold_job(session, job_id, actor_id="ADMIN", reason=reason)
    await session.commit()
    return {"status": "success", "job_id": job.id, "is_held": job.is_held, "reason": job.held_reason}


@router.post("/queue/{job_id}/resume")
async def resume_queue_job(job_id: str, session: AsyncSession = Depends(get_db)):
    job = await resume_job(session, job_id, actor_id="ADMIN")
    await session.commit()
    return {"status": "success", "job_id": job.id, "is_held": job.is_held}


@router.post("/queue/{job_id}/retry")
async def retry_queue_job(job_id: str, session: AsyncSession = Depends(get_db)):
    job = await retry_job(session, job_id, actor_id="ADMIN")
    await session.commit()
    return {"status": "success", "job_id": job.id, "status": job.status}


@router.post("/queue/{job_id}/manual-print")
async def manual_print_queue_job(job_id: str, session: AsyncSession = Depends(get_db)):
    job = await manual_print_job(session, job_id, actor_id="ADMIN")
    await session.commit()
    return {"status": "success", "job_id": job.id, "order_status": job.order.status}


@router.post("/queue/{job_id}/cancel")
async def cancel_queue_job(job_id: str, session: AsyncSession = Depends(get_db)):
    query = select(PrintJob).where(PrintJob.id == job_id).options(selectinload(PrintJob.order), selectinload(PrintJob.queue_entry))
    res = await session.execute(query)
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = "CANCELLED"
    if job.queue_entry:
        job.queue_entry.status = "DEQUEUED"

    await transition_order(session, job.order, OrderState.CANCELLED, actor_type="ADMIN")
    await session.commit()
    return {"status": "success", "job_id": job.id, "order_status": job.order.status}


# 8. Orders & History
@router.get("/orders", response_model=List[OrderRead])
async def list_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    query = (
        select(Order)
        .options(
            selectinload(Order.files).selectinload(OrderFile.configuration),
            selectinload(Order.payments),
            selectinload(Order.user),
        )
        .order_by(Order.created_at.desc())
    )
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(Order.status.in_(statuses))
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Order.order_code.ilike(search_pattern)) | (Order.pickup_token.ilike(search_pattern))
        )

    res = await session.execute(query)
    return res.scalars().all()


@router.get("/orders/{order_id}", response_model=OrderRead)
async def get_order_by_id_endpoint(order_id: str, session: AsyncSession = Depends(get_db)):
    return await get_order_by_id(session, order_id)


@router.post("/orders/{order_id}/collected")
async def mark_order_collected(order_id: str, session: AsyncSession = Depends(get_db)):
    order = await get_order_by_id(session, order_id)
    if order.status != OrderState.READY_FOR_PICKUP.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Order must be in READY_FOR_PICKUP state (currently {order.status})",
        )
    await transition_order(
        session=session,
        order=order,
        target_state=OrderState.COLLECTED,
        actor_type="ADMIN",
    )
    await session.commit()
    return {"status": "success", "order_id": order.id, "state": order.status}


# 9. Reports & Analytics
@router.get("/reports/summary", response_model=ReportsSummaryResponse)
async def get_reports_summary(session: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    # Total revenue 7d
    rev_q = await session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
            Payment.status == "VERIFIED", Payment.verified_at >= seven_days_ago
        )
    )
    total_rev = float(rev_q.scalar_one() or 0.0)

    # Total orders 7d
    ord_q = await session.execute(
        select(func.count(Order.id)).where(Order.created_at >= seven_days_ago)
    )
    total_orders = ord_q.scalar_one() or 0

    # Total pages
    pages_q = await session.execute(
        select(func.coalesce(func.sum(Order.total_pages), 0)).where(Order.created_at >= seven_days_ago)
    )
    total_pages = int(pages_q.scalar_one() or 0)

    avg_val = (total_rev / total_orders) if total_orders > 0 else 0.0

    # Daily breakdown for past 7 days
    daily_rev = []
    daily_vol = []
    for i in range(6, -1, -1):
        day = (now - timedelta(days=i)).date()
        start_d = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end_d = start_d + timedelta(days=1)

        d_rev_q = await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
                Payment.status == "VERIFIED", Payment.verified_at >= start_d, Payment.verified_at < end_d
            )
        )
        d_vol_q = await session.execute(
            select(func.count(Order.id)).where(Order.created_at >= start_d, Order.created_at < end_d)
        )
        daily_rev.append({"date": day.strftime("%b %d"), "amount": float(d_rev_q.scalar_one() or 0.0)})
        daily_vol.append({"date": day.strftime("%b %d"), "orders": int(d_vol_q.scalar_one() or 0)})

    return ReportsSummaryResponse(
        total_revenue_7d=total_rev,
        total_orders_7d=total_orders,
        total_pages_7d=total_pages,
        bw_pages_7d=int(total_pages * 0.8),
        color_pages_7d=int(total_pages * 0.2),
        avg_order_value=avg_val,
        daily_revenue=daily_rev,
        daily_volume=daily_vol,
    )


# 10. Service Controls
@router.post("/printing/start")
@router.post("/print-service/start")
async def start_printing(
    payload: Optional[ServiceControlRequest] = None, session: AsyncSession = Depends(get_db)
):
    res = await session.execute(select(PrintServiceControl).limit(1))
    ctrl = res.scalar_one_or_none()
    if not ctrl:
        ctrl = PrintServiceControl()
        session.add(ctrl)

    ctrl.manual_override = "FORCED_OPEN"
    ctrl.effective_status = "RUNNING"
    ctrl.reason = payload.reason if payload and payload.reason else "Admin forced start"
    ctrl.updated_by = "ADMIN"

    session.add(
        AuditLog(
            event_type="PRINTING_FORCED_OPEN",
            actor_type="ADMIN",
            actor_id="ADMIN",
            metadata_json=f'{{"reason": "{ctrl.reason}"}}',
        )
    )
    await session.commit()
    return {"status": "success", "manual_override": ctrl.manual_override, "effective_status": ctrl.effective_status}


@router.post("/printing/stop")
@router.post("/print-service/stop")
async def stop_printing(
    payload: Optional[ServiceControlRequest] = None, session: AsyncSession = Depends(get_db)
):
    res = await session.execute(select(PrintServiceControl).limit(1))
    ctrl = res.scalar_one_or_none()
    if not ctrl:
        ctrl = PrintServiceControl()
        session.add(ctrl)

    ctrl.manual_override = "PAUSED"
    ctrl.effective_status = "PAUSED"
    ctrl.reason = payload.reason if payload and payload.reason else "Admin paused printing"
    ctrl.updated_by = "ADMIN"

    session.add(
        AuditLog(
            event_type="PRINTING_STOPPED",
            actor_type="ADMIN",
            actor_id="ADMIN",
            metadata_json=f'{{"reason": "{ctrl.reason}"}}',
        )
    )
    await session.commit()
    return {"status": "success", "manual_override": ctrl.manual_override, "effective_status": ctrl.effective_status}


@router.post("/printing/emergency-stop")
async def emergency_stop_printing(
    payload: Optional[ServiceControlRequest] = None, session: AsyncSession = Depends(get_db)
):
    res = await session.execute(select(PrintServiceControl).limit(1))
    ctrl = res.scalar_one_or_none()
    if not ctrl:
        ctrl = PrintServiceControl()
        session.add(ctrl)

    ctrl.manual_override = "EMERGENCY_STOP"
    ctrl.effective_status = "PAUSED"
    ctrl.reason = payload.reason if payload and payload.reason else "Emergency Stop Triggered"
    ctrl.updated_by = "ADMIN"

    session.add(
        AuditLog(
            event_type="EMERGENCY_STOP",
            actor_type="ADMIN",
            actor_id="ADMIN",
            metadata_json=f'{{"reason": "{ctrl.reason}"}}',
        )
    )
    await session.commit()
    return {"status": "success", "manual_override": ctrl.manual_override, "effective_status": ctrl.effective_status}


@router.post("/printing/resume-schedule")
async def resume_schedule(session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(PrintServiceControl).limit(1))
    ctrl = res.scalar_one_or_none()
    if not ctrl:
        ctrl = PrintServiceControl()
        session.add(ctrl)

    ctrl.manual_override = "AUTO"
    ctrl.reason = "Returned to automatic schedule"
    ctrl.updated_by = "ADMIN"

    now = datetime.now(timezone.utc)
    state = await get_effective_print_state(session, now)
    ctrl.effective_status = state["effective_status"]

    session.add(
        AuditLog(
            event_type="PRINTING_RETURNED_TO_SCHEDULE",
            actor_type="ADMIN",
            actor_id="ADMIN",
        )
    )
    await session.commit()
    return {"status": "success", "manual_override": "AUTO", "effective_status": ctrl.effective_status}


@router.get("/printing/status")
@router.get("/print-service/status")
async def get_printing_status(session: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    return await get_effective_print_state(session, now)


# 11. Schedules & Exceptions
@router.get("/schedule", response_model=List[PrintingScheduleItem])
async def get_schedules(session: AsyncSession = Depends(get_db)):
    await seed_default_schedule_if_empty(session)
    res = await session.execute(select(PrintingSchedule).order_by(PrintingSchedule.day_of_week.asc()))
    schedules = res.scalars().all()
    return [
        PrintingScheduleItem(
            day_of_week=s.day_of_week,
            day_name=DAY_NAMES[s.day_of_week] if 0 <= s.day_of_week < 7 else f"Day {s.day_of_week}",
            open_time=s.open_time.strftime("%H:%M"),
            close_time=s.close_time.strftime("%H:%M"),
            enabled=s.enabled,
        )
        for s in schedules
    ]


@router.put("/schedule")
@router.patch("/schedule")
async def update_schedules(payload: ScheduleUpdateRequest, session: AsyncSession = Depends(get_db)):
    for item in payload.schedules:
        res = await session.execute(
            select(PrintingSchedule).where(PrintingSchedule.day_of_week == item.day_of_week)
        )
        sched = res.scalar_one_or_none()
        open_parts = [int(p) for p in item.open_time.split(":")]
        close_parts = [int(p) for p in item.close_time.split(":")]
        o_time = time(open_parts[0], open_parts[1])
        c_time = time(close_parts[0], close_parts[1])

        if sched:
            sched.open_time = o_time
            sched.close_time = c_time
            sched.enabled = item.enabled
        else:
            sched = PrintingSchedule(
                day_of_week=item.day_of_week,
                open_time=o_time,
                close_time=c_time,
                enabled=item.enabled,
            )
            session.add(sched)

    session.add(
        AuditLog(
            event_type="SCHEDULE_UPDATED",
            actor_type="ADMIN",
            actor_id="ADMIN",
        )
    )
    await session.commit()
    return {"status": "success", "updated_count": len(payload.schedules)}


@router.get("/schedule/exceptions", response_model=List[ScheduleExceptionRead])
async def list_schedule_exceptions(session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(ScheduleException).order_by(ScheduleException.exception_date.asc()))
    exceptions = res.scalars().all()
    return [
        ScheduleExceptionRead(
            id=e.id,
            exception_date=e.exception_date,
            exception_type=e.exception_type,
            open_time=e.open_time.strftime("%H:%M") if e.open_time else None,
            close_time=e.close_time.strftime("%H:%M") if e.close_time else None,
            reason=e.reason,
            created_at=e.created_at,
        )
        for e in exceptions
    ]


@router.post("/schedule/exceptions", response_model=ScheduleExceptionRead)
async def create_schedule_exception(
    payload: ScheduleExceptionCreate, session: AsyncSession = Depends(get_db)
):
    o_time = None
    c_time = None
    if payload.open_time:
        parts = [int(p) for p in payload.open_time.split(":")]
        o_time = time(parts[0], parts[1])
    if payload.close_time:
        parts = [int(p) for p in payload.close_time.split(":")]
        c_time = time(parts[0], parts[1])

    exc = ScheduleException(
        exception_date=payload.exception_date,
        exception_type=payload.exception_type,
        open_time=o_time,
        close_time=c_time,
        reason=payload.reason,
    )
    session.add(exc)
    session.add(
        AuditLog(
            event_type=f"EXCEPTION_ADDED_{payload.exception_type}",
            actor_type="ADMIN",
            actor_id="ADMIN",
            metadata_json=f'{{"date": "{payload.exception_date}", "reason": "{payload.reason}"}}',
        )
    )
    await session.commit()
    return ScheduleExceptionRead(
        id=exc.id,
        exception_date=exc.exception_date,
        exception_type=exc.exception_type,
        open_time=exc.open_time.strftime("%H:%M") if exc.open_time else None,
        close_time=exc.close_time.strftime("%H:%M") if exc.close_time else None,
        reason=exc.reason,
        created_at=exc.created_at,
    )


@router.delete("/schedule/exceptions/{exception_id}")
async def delete_schedule_exception(exception_id: str, session: AsyncSession = Depends(get_db)):
    res = await session.execute(
        select(ScheduleException).where(ScheduleException.id == exception_id)
    )
    exc = res.scalar_one_or_none()
    if not exc:
        raise HTTPException(status_code=404, detail="Schedule exception not found")

    await session.delete(exc)
    session.add(
        AuditLog(
            event_type="EXCEPTION_DELETED",
            actor_type="ADMIN",
            actor_id="ADMIN",
            metadata_json=f'{{"id": "{exception_id}"}}',
        )
    )
    await session.commit()
    return {"status": "success", "deleted_id": exception_id}


# 12. Settings & Pricing
@router.get("/settings/shop")
async def get_shop_settings(session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(ShopSetting).limit(1))
    s = res.scalar_one_or_none()
    if not s:
        s = ShopSetting(
            shop_name="Print Express Hub",
            timezone="Asia/Kolkata",
            currency="INR",
            order_acceptance_enabled=True,
        )
        session.add(s)
        await session.commit()
    return {
        "shop_name": s.shop_name,
        "timezone": s.timezone,
        "currency": s.currency,
        "order_acceptance_enabled": s.order_acceptance_enabled,
    }


@router.put("/settings/shop")
async def update_shop_settings(
    payload: ShopSettingsUpdateRequest, session: AsyncSession = Depends(get_db)
):
    res = await session.execute(select(ShopSetting).limit(1))
    s = res.scalar_one_or_none()
    if not s:
        s = ShopSetting()
        session.add(s)
    s.shop_name = payload.shop_name
    s.timezone = payload.timezone
    s.currency = payload.currency
    s.order_acceptance_enabled = payload.order_acceptance_enabled
    await session.commit()
    return {"status": "success", "settings": payload.model_dump()}


@router.get("/pricing")
async def get_pricing():
    return {
        "base_charge": settings.PRICE_FILE_BASE_CHARGE,
        "bw_single": settings.PRICE_BW_SINGLE,
        "bw_double": settings.PRICE_BW_DOUBLE,
        "color_single": settings.PRICE_COLOR_SINGLE,
        "color_double": settings.PRICE_COLOR_DOUBLE,
        "currency": settings.CURRENCY,
    }


@router.put("/pricing")
async def update_pricing(payload: PricingUpdateRequest, session: AsyncSession = Depends(get_db)):
    settings.PRICE_FILE_BASE_CHARGE = payload.base_charge
    settings.PRICE_BW_SINGLE = payload.bw_single
    settings.PRICE_BW_DOUBLE = payload.bw_double
    settings.PRICE_COLOR_SINGLE = payload.color_single
    settings.PRICE_COLOR_DOUBLE = payload.color_double
    settings.CURRENCY = payload.currency

    session.add(
        AuditLog(
            event_type="PRICING_UPDATED",
            actor_type="ADMIN",
            actor_id="ADMIN",
        )
    )
    await session.commit()
    return {"status": "success", "pricing": payload.model_dump()}


# 13. Audit Logs
@router.get("/audit-logs", response_model=List[AuditLogRead])
async def list_audit_logs(
    limit: int = Query(50, le=200), session: AsyncSession = Depends(get_db)
):
    res = await session.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    )
    return res.scalars().all()
