import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple
import fitz  # PyMuPDF
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import settings
from backend.core.exceptions import AppException, ErrorCode
from backend.core.logging import logger
from backend.core.security import sanitize_filename
from backend.models.configuration import PrintConfiguration
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.services.orders.state_machine import OrderState, transition_order


def extract_pdf_page_count_and_validate(file_bytes: bytes) -> int:
    """
    Validates PDF integrity and extracts page count using PyMuPDF.
    Raises AppException for corrupt, password-protected, or zero-page PDFs.
    """
    if not file_bytes or len(file_bytes) == 0:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message="Uploaded document is empty (0 bytes).",
        )

    # Check PDF magic bytes (%PDF-)
    if not file_bytes.startswith(b"%PDF-"):
        raise AppException(
            code=ErrorCode.UNSUPPORTED_FILE,
            message="Uploaded file does not have a valid PDF header.",
        )

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message=f"Failed to parse PDF document: {str(e)}",
        )

    try:
        if doc.is_encrypted:
            raise AppException(
                code=ErrorCode.PASSWORD_PROTECTED_FILE,
                message="PDF is password protected. Please upload an unlocked PDF.",
            )

        page_count = doc.page_count
        if page_count <= 0:
            raise AppException(
                code=ErrorCode.ZERO_PAGE_FILE,
                message="PDF document contains zero pages.",
            )

        return page_count
    finally:
        doc.close()


async def process_and_save_order_file(
    session: AsyncSession,
    order: Order,
    original_filename: str,
    file_bytes: bytes,
    mime_type: str = "application/pdf",
) -> OrderFile:
    """
    Validates PDF, assigns upload sequence, calculates hash, writes to isolated private storage,
    creates default PrintConfiguration, and updates Order state.
    """
    # Verify order state allows file uploading
    if order.status not in [
        OrderState.CREATED.value,
        OrderState.FILES_UPLOADING.value,
        OrderState.FILES_UPLOADED.value,
        OrderState.CONFIGURATION_PENDING.value,
    ]:
        raise AppException(
            code=ErrorCode.ORDER_LOCKED,
            message=f"Cannot upload files to order in status {order.status}",
        )

    # Validate file size
    file_size = len(file_bytes)
    if file_size > settings.MAX_FILE_SIZE_BYTES:
        raise AppException(
            code=ErrorCode.FILE_TOO_LARGE,
            message=f"File size {file_size} exceeds maximum limit of {settings.MAX_FILE_SIZE_BYTES} bytes",
        )

    # Check total files limit
    seq_query = select(func.count(OrderFile.id)).where(OrderFile.order_id == order.id, OrderFile.status != "DELETED")
    count_res = await session.execute(seq_query)
    current_count = count_res.scalar_one() or 0
    if current_count >= settings.MAX_FILES_PER_ORDER:
        raise AppException(
            code=ErrorCode.TOO_MANY_FILES,
            message=f"Maximum of {settings.MAX_FILES_PER_ORDER} files per order reached.",
        )

    # Validate PDF and extract pages
    page_count = extract_pdf_page_count_and_validate(file_bytes)

    # Calculate SHA256
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Determine upload sequence (1-indexed)
    upload_sequence = current_count + 1
    file_id = str(uuid.uuid4())

    # Write file to isolated private storage
    order_dir = settings.storage_path / "orders" / order.id
    order_dir.mkdir(parents=True, exist_ok=True)
    storage_filename = f"{file_id}.pdf"
    storage_path = str(order_dir / storage_filename)

    with open(storage_path, "wb") as f:
        f.write(file_bytes)

    # Create OrderFile model
    now = datetime.now(timezone.utc)
    order_file = OrderFile(
        id=file_id,
        order_id=order.id,
        upload_sequence=upload_sequence,
        original_filename=sanitize_filename(original_filename),
        file_hash=file_hash,
        mime_type=mime_type,
        storage_path=storage_path,
        page_count=page_count,
        file_size=file_size,
        status="VALIDATED",
        validated_at=now,
    )
    session.add(order_file)

    # Create default configuration for this file
    default_config = PrintConfiguration(
        order_id=order.id,
        file_id=file_id,
        color_mode="bw",
        default_sides="single",
        rules_json="[]",
    )
    session.add(default_config)

    # Update order total pages
    order.total_pages += page_count

    # State transition if still in CREATED
    if order.status == OrderState.CREATED.value:
        await transition_order(
            session=session,
            order=order,
            target_state=OrderState.FILES_UPLOADING,
            actor_type="CUSTOMER",
            metadata={"first_file": original_filename},
        )

    await session.flush()
    return order_file
