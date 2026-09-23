import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import settings
from backend.core.logging import logger
from backend.models.queue import PrintJob, QueueEntry
from backend.models.schedule import (
    PrintServiceControl,
    PrintingSchedule,
    ScheduleException,
    ShopSetting,
)


async def seed_default_schedule_if_empty(session: AsyncSession) -> None:
    """Initialize default shop settings, weekly schedule, and control records if empty."""
    # 1. Shop settings
    res = await session.execute(select(ShopSetting).limit(1))
    shop_setting = res.scalar_one_or_none()
    if not shop_setting:
        shop_setting = ShopSetting(
            shop_name="Print Express Hub",
            timezone="Asia/Kolkata",
            currency="INR",
            order_acceptance_enabled=True,
        )
        session.add(shop_setting)

    # 2. Printing Schedules (Mon-Sun 08:00 - 20:00)
    sched_res = await session.execute(select(func.count(PrintingSchedule.id)))
    count = sched_res.scalar_one() or 0
    if count == 0:
        for day in range(7):
            session.add(
                PrintingSchedule(
                    day_of_week=day,
                    open_time=time(8, 0),
                    close_time=time(20, 0),
                    enabled=True,
                )
            )

    # 3. Print Service Control
    ctrl_res = await session.execute(select(PrintServiceControl).limit(1))
    ctrl = ctrl_res.scalar_one_or_none()
    if not ctrl:
        ctrl = PrintServiceControl(
            manual_override="AUTO",
            effective_status="RUNNING",
            reason="Initial system startup",
            updated_by="SYSTEM",
        )
        session.add(ctrl)

    await session.commit()
    logger.info("Default shop settings, schedule, and controls seeded successfully.")


async def get_effective_print_state(
    session: AsyncSession, check_time: Optional[datetime] = None
) -> Dict[str, any]:
    """
    Evaluates current operational state:
    Manual Override (AUTO / PAUSED / FORCED_OPEN / EMERGENCY_STOP)
    Weekly schedule, exceptions (HOLIDAY / CLOSED / SPECIAL_HOURS), and shop settings.
    """
    if check_time is None:
        check_time = datetime.now(timezone.utc)

    # Get Control
    ctrl_res = await session.execute(
        select(PrintServiceControl).order_by(PrintServiceControl.updated_at.desc()).limit(1)
    )
    ctrl = ctrl_res.scalar_one_or_none()
    manual_override = ctrl.manual_override if ctrl else "AUTO"

    # Emergency stop or manual pause
    if manual_override == "EMERGENCY_STOP":
        return {
            "shop_open": False,
            "manual_override": "EMERGENCY_STOP",
            "effective_status": "PAUSED",
            "reason": ctrl.reason or "Emergency Stop Triggered",
        }

    if manual_override == "PAUSED":
        return {
            "shop_open": False,
            "manual_override": "PAUSED",
            "effective_status": "PAUSED",
            "reason": ctrl.reason or "Printing paused manually by shop owner",
        }

    if manual_override == "FORCED_OPEN":
        return {
            "shop_open": True,
            "manual_override": "FORCED_OPEN",
            "effective_status": "RUNNING",
            "reason": "Shop forced open manually by shop owner",
        }

    # AUTO: check calendar & hours
    is_open, reason = await is_inside_print_window(session, check_time)
    effective_status = "RUNNING" if is_open else "CLOSED"

    return {
        "shop_open": is_open,
        "manual_override": "AUTO",
        "effective_status": effective_status,
        "reason": reason,
    }


async def is_inside_print_window(
    session: AsyncSession, check_dt: datetime
) -> Tuple[bool, str]:
    """
    Checks if check_dt falls within a valid print window,
    checking exception overrides first, then standard weekly schedule.
    """
    target_date = check_dt.date()
    target_time = check_dt.time()
    day_of_week = check_dt.weekday()

    # Check date exceptions
    exc_res = await session.execute(
        select(ScheduleException).where(ScheduleException.exception_date == target_date)
    )
    exc = exc_res.scalar_one_or_none()

    if exc:
        if exc.exception_type in ["CLOSED", "HOLIDAY"]:
            return False, f"Shop is closed ({exc.exception_type}: {exc.reason or 'Holiday'})"
        elif exc.exception_type == "SPECIAL_HOURS":
            if exc.open_time and exc.close_time:
                if exc.open_time <= exc.close_time:
                    if exc.open_time <= target_time <= exc.close_time:
                        return True, "Inside special hours window"
                    return False, f"Outside special hours ({exc.open_time.strftime('%H:%M')} - {exc.close_time.strftime('%H:%M')})"
                else:
                    # Overnight window
                    if target_time >= exc.open_time or target_time <= exc.close_time:
                        return True, "Inside overnight special hours window"
                    return False, "Outside overnight special hours"

    # Check weekly schedule
    sched_res = await session.execute(
        select(PrintingSchedule).where(PrintingSchedule.day_of_week == day_of_week)
    )
    sched = sched_res.scalar_one_or_none()

    if not sched:
        # Default fallback 08:00 to 20:00 if table not yet seeded
        if time(8, 0) <= target_time <= time(20, 0):
            return True, "Inside regular operating hours"
        return False, "Outside default operating hours (08:00 - 20:00)"

    if not sched.enabled:
        return False, f"Printing is disabled on {check_dt.strftime('%A')}"

    # Handle standard vs overnight hours
    if sched.open_time <= sched.close_time:
        if sched.open_time <= target_time <= sched.close_time:
            return True, "Inside regular operating hours"
        return False, f"Outside operating hours ({sched.open_time.strftime('%H:%M')} - {sched.close_time.strftime('%H:%M')})"
    else:
        # Overnight window e.g. 22:00 to 02:00
        if target_time >= sched.open_time or target_time <= sched.close_time:
            return True, "Inside overnight operating hours"
        return False, f"Outside overnight operating hours ({sched.open_time.strftime('%H:%M')} - {sched.close_time.strftime('%H:%M')})"


async def get_next_print_window(
    session: AsyncSession, check_dt: datetime
) -> Optional[datetime]:
    """Finds the next datetime when the shop opens, searching up to 14 days ahead."""
    cur_date = check_dt.date()

    for day_offset in range(14):
        eval_date = cur_date + timedelta(days=day_offset)
        day_of_week = eval_date.weekday()

        # Check exception for eval_date
        exc_res = await session.execute(
            select(ScheduleException).where(ScheduleException.exception_date == eval_date)
        )
        exc = exc_res.scalar_one_or_none()

        if exc:
            if exc.exception_type in ["CLOSED", "HOLIDAY"]:
                continue
            elif exc.exception_type == "SPECIAL_HOURS" and exc.open_time:
                open_dt = datetime.combine(eval_date, exc.open_time, tzinfo=check_dt.tzinfo or timezone.utc)
                if open_dt > check_dt:
                    return open_dt
                continue

        # Check standard schedule
        sched_res = await session.execute(
            select(PrintingSchedule).where(PrintingSchedule.day_of_week == day_of_week)
        )
        sched = sched_res.scalar_one_or_none()

        if sched and sched.enabled:
            open_dt = datetime.combine(eval_date, sched.open_time, tzinfo=check_dt.tzinfo or timezone.utc)
            if open_dt > check_dt:
                return open_dt

    return None


async def calculate_estimated_ready_time(
    session: AsyncSession,
    total_pages: int,
    queue_position: Optional[int] = None,
    check_time: Optional[datetime] = None,
) -> Dict[str, any]:
    """
    Computes schedule-aware ETA accounting for operating windows,
    queue ahead duration, and print duration.
    """
    if check_time is None:
        check_time = datetime.now(timezone.utc)

    # Print duration for this job
    warmup_seconds = 10
    ppm = max(settings.PRINTER_SPEED_PPM, 1)
    own_print_seconds = warmup_seconds + math.ceil((total_pages / ppm) * 60)
    own_print_minutes = math.ceil(own_print_seconds / 60)

    # Queue delay ahead
    queue_query = (
        select(func.coalesce(func.sum(PrintJob.estimated_duration_seconds), 0))
        .join(QueueEntry, QueueEntry.print_job_id == PrintJob.id)
        .where(QueueEntry.status.in_(["WAITING", "PROCESSING"]))
    )
    if queue_position:
        queue_query = queue_query.where(QueueEntry.position < queue_position)

    res = await session.execute(queue_query)
    queue_ahead_seconds = res.scalar_one() or 0
    queue_before_order_minutes = math.ceil(queue_ahead_seconds / 60)

    total_work_seconds = queue_ahead_seconds + own_print_seconds

    # Check effective state
    effective_state = await get_effective_print_state(session, check_time)

    if effective_state["shop_open"] and effective_state["effective_status"] == "RUNNING":
        ready_time = check_time + timedelta(seconds=total_work_seconds)
        return {
            "estimated_ready_at": ready_time.isoformat(),
            "status": "QUEUED",
            "next_print_window_start": None,
            "queue_before_order_minutes": queue_before_order_minutes,
            "own_print_minutes": own_print_minutes,
            "reason": "Shop open and running",
        }
    else:
        # Outside operating window or paused
        next_window = await get_next_print_window(session, check_time)
        base_time = next_window if next_window else (check_time + timedelta(hours=1))
        ready_time = base_time + timedelta(seconds=total_work_seconds)

        return {
            "estimated_ready_at": ready_time.isoformat(),
            "status": "WAITING_FOR_PRINT_WINDOW",
            "next_print_window_start": next_window.isoformat() if next_window else None,
            "queue_before_order_minutes": queue_before_order_minutes,
            "own_print_minutes": own_print_minutes,
            "reason": effective_state["reason"],
        }
