from typing import List, Optional
from pydantic import BaseModel, Field


class FilePricingDetail(BaseModel):
    file_id: str
    upload_sequence: int
    filename: str
    page_count: int
    bw_single_pages: int = 0
    bw_double_pages: int = 0
    color_single_pages: int = 0
    color_double_pages: int = 0
    file_cost: float = 0.0


class PriceSnapshot(BaseModel):
    file_count: int
    file_base_charge: float
    total_pages: int
    printing_charge: float
    total_amount: float
    currency: str = "INR"
    pricing_version: str = "v1"
    details: List[FilePricingDetail] = Field(default_factory=list)
