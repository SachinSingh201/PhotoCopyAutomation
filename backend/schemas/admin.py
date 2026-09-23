from datetime import date, datetime, time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SystemStatusResponse(BaseModel):
    agent_status: str
    printers_connected: int
    jobs_needing_review: int
    effective_print_status: str
    manual_override: str
    active_window: str
    next_window: Optional[str] = None


class SearchResultItem(BaseModel):
    type: str  # "order", "file", "customer"
    id: str
    title: str
    subtitle: str
    token: Optional[str] = None
    status: Optional[str] = None


class NotificationItem(BaseModel):
    id: str
    order_id: Optional[str] = None
    title: str
    message: str
    type: str
    status: str
    is_read: bool
    created_at: datetime


class CurrentPrintJobDetail(BaseModel):
    job_found: bool
    job_id: Optional[str] = None
    order_id: Optional[str] = None
    order_code: Optional[str] = None
    pickup_token: Optional[str] = None
    primary_filename: Optional[str] = None
    file_count: int = 0
    total_pages: int = 0
    color_mode: str = "bw"
    default_sides: str = "single"
    started_at: Optional[datetime] = None
    estimated_duration_seconds: int = 0
    estimated_completion_at: Optional[datetime] = None


class AlertItem(BaseModel):
    id: str
    severity: str  # "high", "medium", "low"
    type: str  # "MANUAL_REQUIRED", "PRINT_FAILED", "HEARTBEAT_DELAY", "PAYMENT_ANOMALY"
    title: str
    description: str
    order_id: Optional[str] = None
    job_id: Optional[str] = None
    token: Optional[str] = None
    created_at: datetime


class PrinterItem(BaseModel):
    id: str
    name: str
    model: str
    connection_type: str
    ip_address: Optional[str] = None
    status: str  # "connected", "available", "driver_missing", "offline"
    is_default: bool
    supports_color: bool
    supports_duplex: bool
    paper_tray_status: Optional[str] = None
    toner_level: Optional[int] = None
    last_test_page_at: Optional[datetime] = None


class PrinterPatchRequest(BaseModel):
    is_default: Optional[bool] = None
    status: Optional[str] = None


class AdminDashboardStats(BaseModel):
    orders_today: int
    waiting_count: int
    printing_count: int
    ready_count: int
    collected_today: int
    revenue_today: float
    pages_printed_today: int
    avg_wait_seconds: float
    failed_jobs_count: int
    manual_jobs_count: int
    agent_status: str
    printer_status: str
    effective_print_status: str
    manual_override: str
    currency: str = "INR"


class AdminQueueItem(BaseModel):
    job_id: str
    order_id: str
    order_code: str
    pickup_token: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    queue_position: int
    priority: int = 0
    status: str
    is_held: bool = False
    held_reason: Optional[str] = None
    total_pages: int
    file_count: int
    payment_status: str
    estimated_duration_seconds: int
    eta_seconds: int = 0
    print_mode: str = "AUTO"
    enqueued_at: datetime


class HoldJobRequest(BaseModel):
    reason: Optional[str] = "Admin manual hold"


class PrintingScheduleItem(BaseModel):
    day_of_week: int
    day_name: Optional[str] = None
    open_time: str  # "HH:MM"
    close_time: str  # "HH:MM"
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    schedules: List[PrintingScheduleItem]


class ScheduleExceptionCreate(BaseModel):
    exception_date: date
    exception_type: str  # CLOSED, HOLIDAY, SPECIAL_HOURS
    open_time: Optional[str] = None  # "HH:MM"
    close_time: Optional[str] = None  # "HH:MM"
    reason: Optional[str] = None


class ScheduleExceptionRead(BaseModel):
    id: str
    exception_date: date
    exception_type: str
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime


class ServiceControlRequest(BaseModel):
    reason: Optional[str] = None


class PricingUpdateRequest(BaseModel):
    base_charge: float
    bw_single: float
    bw_double: float
    color_single: float
    color_double: float
    currency: str = "INR"


class ShopSettingsUpdateRequest(BaseModel):
    shop_name: str
    timezone: str
    currency: str
    order_acceptance_enabled: bool


class ReportsSummaryResponse(BaseModel):
    total_revenue_7d: float
    total_orders_7d: int
    total_pages_7d: int
    bw_pages_7d: int
    color_pages_7d: int
    avg_order_value: float
    daily_revenue: List[Dict[str, Any]]
    daily_volume: List[Dict[str, Any]]


class AuditLogRead(BaseModel):
    id: str
    order_id: Optional[str]
    event_type: str
    actor_type: str
    actor_id: Optional[str]
    metadata_json: Optional[str]
    created_at: datetime
