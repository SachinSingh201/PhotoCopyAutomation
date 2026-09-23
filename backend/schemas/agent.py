from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class AgentHeartbeatRequest(BaseModel):
    agent_id: str
    status: str = "ONLINE"  # ONLINE, BUSY
    printer_status: str = "READY"  # READY, PRINTING, ERROR, OUT_OF_PAPER
    metadata: Optional[dict] = None


class AgentHeartbeatResponse(BaseModel):
    acknowledged: bool
    server_time: datetime
    pending_queue_count: int


class JobClaimRequest(BaseModel):
    agent_id: str


class PrintJobFileDetail(BaseModel):
    file_id: str
    upload_sequence: int
    filename: str
    storage_path: str
    page_count: int
    color_mode: str
    default_sides: str
    rules_json: str


class JobClaimResponse(BaseModel):
    job_found: bool
    job_id: Optional[str] = None
    order_id: Optional[str] = None
    order_code: Optional[str] = None
    pickup_token: Optional[str] = None
    files: List[PrintJobFileDetail] = []


class JobStatusUpdateRequest(BaseModel):
    agent_id: str
    status: str  # STARTED, SUCCESS, FAILED
    error_code: Optional[str] = None
    error_message: Optional[str] = None
