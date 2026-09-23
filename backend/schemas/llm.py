from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class PageRule(BaseModel):
    page_start: int = Field(ge=1, description="1-indexed starting page number")
    page_end: Optional[int] = Field(default=None, description="Ending page number (null for end of document)")
    sides: Literal["single", "double"] = Field(description="Single-sided (simplex) or Double-sided (duplex)")
    color: Optional[Literal["bw", "color"]] = Field(default=None, description="Optional color override for this range")


class FilePrintRule(BaseModel):
    file_reference: int = Field(ge=1, description="1-based file upload sequence index (e.g. 1 for File 1)")
    color_mode: Optional[Literal["bw", "color"]] = Field(default="bw", description="Overall file color mode")
    default_sides: Optional[Literal["single", "double"]] = Field(default="single", description="Default side configuration")
    rules: List[PageRule] = Field(default_factory=list, description="Specific page range rules")


class LLMInterpretationResult(BaseModel):
    file_rules: List[FilePrintRule] = Field(default_factory=list, description="Rules mapped per referenced file")
    ambiguity: bool = Field(default=False, description="True if customer instructions cannot be deterministically resolved")
    clarification_question: Optional[str] = Field(default=None, description="Question to ask the user if ambiguity is true")
