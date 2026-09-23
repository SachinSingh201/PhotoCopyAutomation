from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    # File Errors
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FILE = "UNSUPPORTED_FILE"
    CORRUPT_FILE = "CORRUPT_FILE"
    PASSWORD_PROTECTED_FILE = "PASSWORD_PROTECTED_FILE"
    ZERO_PAGE_FILE = "ZERO_PAGE_FILE"
    TOO_MANY_FILES = "TOO_MANY_FILES"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_ACCESS_DENIED = "FILE_ACCESS_DENIED"

    # LLM / NLP Errors
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"
    LLM_AMBIGUOUS = "LLM_AMBIGUOUS"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"

    # Order & Configuration Errors
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    INVALID_PAGE_RANGE = "INVALID_PAGE_RANGE"
    ORDER_LOCKED = "ORDER_LOCKED"
    ORDER_EXPIRED = "ORDER_EXPIRED"

    # Pricing & Payment Errors
    PRICE_MISMATCH = "PRICE_MISMATCH"
    PAYMENT_NOT_VERIFIED = "PAYMENT_NOT_VERIFIED"
    PAYMENT_AMOUNT_MISMATCH = "PAYMENT_AMOUNT_MISMATCH"
    PAYMENT_ALREADY_PROCESSED = "PAYMENT_ALREADY_PROCESSED"
    UNAUTHORIZED_WEBHOOK = "UNAUTHORIZED_WEBHOOK"

    # Queue & Printer Errors
    QUEUE_CONFLICT = "QUEUE_CONFLICT"
    AGENT_OFFLINE = "AGENT_OFFLINE"
    PRINTER_OFFLINE = "PRINTER_OFFLINE"
    PRINT_FAILED = "PRINT_FAILED"
    PRINT_RESULT_UNKNOWN = "PRINT_RESULT_UNKNOWN"
    JOB_ALREADY_CLAIMED = "JOB_ALREADY_CLAIMED"

    # System & Cleanup Errors
    CLEANUP_FAILED = "CLEANUP_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppException(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = 400,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
