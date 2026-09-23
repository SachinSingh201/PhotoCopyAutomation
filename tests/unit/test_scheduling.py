from datetime import date, datetime, time, timezone
import pytest
from sqlalchemy import select
from backend.models.schedule import (
    PrintServiceControl,
    PrintingSchedule,
    ScheduleException,
    ShopSetting,
)
from backend.services.scheduling.service import (
    calculate_estimated_ready_time,
    get_effective_print_state,
    get_next_print_window,
    is_inside_print_window,
    seed_default_schedule_if_empty,
)


@pytest.mark.asyncio
async def test_schedule_seeding_and_operating_hours(db_session):
    # Seed default schedule
    await seed_default_schedule_if_empty(db_session)

    # Monday 10:00 UTC (inside 08:00 - 20:00)
    mon_open = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    is_open, reason = await is_inside_print_window(db_session, mon_open)
    assert is_open is True
    assert "Inside regular operating hours" in reason

    # Monday 23:00 UTC (outside 08:00 - 20:00)
    mon_closed = datetime(2026, 9, 21, 23, 0, tzinfo=timezone.utc)
    is_open, reason = await is_inside_print_window(db_session, mon_closed)
    assert is_open is False
    assert "Outside operating hours" in reason

    # Check next window from 23:00 -> should be next morning at 08:00
    next_win = await get_next_print_window(db_session, mon_closed)
    assert next_win is not None
    assert next_win.day == 22
    assert next_win.hour == 8
    assert next_win.minute == 0


@pytest.mark.asyncio
async def test_schedule_holiday_exception(db_session):
    await seed_default_schedule_if_empty(db_session)

    holiday_date = date(2026, 10, 2)
    exc = ScheduleException(
        exception_date=holiday_date,
        exception_type="HOLIDAY",
        reason="National Holiday",
    )
    db_session.add(exc)
    await db_session.flush()

    check_dt = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)
    is_open, reason = await is_inside_print_window(db_session, check_dt)
    assert is_open is False
    assert "HOLIDAY" in reason


@pytest.mark.asyncio
async def test_manual_service_overrides(db_session):
    await seed_default_schedule_if_empty(db_session)

    # 1. Force Open override
    ctrl_res = await db_session.execute(select(PrintServiceControl).limit(1))
    ctrl = ctrl_res.scalar_one()
    ctrl.manual_override = "FORCED_OPEN"
    ctrl.effective_status = "RUNNING"
    ctrl.reason = "Admin forced start"
    await db_session.commit()

    check_midnight = datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
    state = await get_effective_print_state(db_session, check_midnight)
    assert state["shop_open"] is True
    assert state["effective_status"] == "RUNNING"
    assert state["manual_override"] == "FORCED_OPEN"

    # 2. Emergency stop override
    ctrl.manual_override = "EMERGENCY_STOP"
    await db_session.commit()

    state = await get_effective_print_state(db_session, check_midnight)
    assert state["shop_open"] is False
    assert state["effective_status"] == "PAUSED"
    assert state["manual_override"] == "EMERGENCY_STOP"


@pytest.mark.asyncio
async def test_eta_calculation_open_vs_closed(db_session):
    await seed_default_schedule_if_empty(db_session)

    # Open hours ETA (20 pages = 10s warmup + 60s print = 70s = 2 mins)
    open_dt = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    res_open = await calculate_estimated_ready_time(db_session, total_pages=20, check_time=open_dt)
    assert res_open["status"] == "QUEUED"
    assert res_open["own_print_minutes"] == 2

    # Closed hours ETA at 23:00 -> status is WAITING_FOR_PRINT_WINDOW
    closed_dt = datetime(2026, 9, 21, 23, 0, tzinfo=timezone.utc)
    res_closed = await calculate_estimated_ready_time(db_session, total_pages=20, check_time=closed_dt)
    assert res_closed["status"] == "WAITING_FOR_PRINT_WINDOW"
    assert res_closed["next_print_window_start"] is not None
