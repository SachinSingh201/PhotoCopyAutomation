from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class QueueItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    order_id: str
    order_code: str
    pickup_token: Optional[str] = None
    queue_position: int
    status: str
    total_pages: int
    estimated_duration_seconds: int
    enqueued_at: datetime
