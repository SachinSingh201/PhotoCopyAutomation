from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class OrderFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    upload_sequence: int
    original_filename: str
    file_hash: str
    mime_type: str
    page_count: int
    file_size: int
    status: str
    created_at: datetime
    validated_at: Optional[datetime] = None
