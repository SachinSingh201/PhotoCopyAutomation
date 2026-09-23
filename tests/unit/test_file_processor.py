import pytest
from backend.core.exceptions import AppException, ErrorCode
from backend.models.order import Order
from backend.models.user import User
from backend.services.files.service import extract_pdf_page_count_and_validate, process_and_save_order_file


def test_extract_pdf_page_count_valid(valid_pdf_6_pages):
    count = extract_pdf_page_count_and_validate(valid_pdf_6_pages)
    assert count == 6


def test_extract_pdf_page_count_corrupt():
    with pytest.raises(AppException) as exc_info:
        extract_pdf_page_count_and_validate(b"%PDF-corrupt-data-not-a-real-pdf")
    assert exc_info.value.code in [ErrorCode.CORRUPT_FILE, ErrorCode.UNSUPPORTED_FILE]


def test_extract_pdf_page_count_empty():
    with pytest.raises(AppException) as exc_info:
        extract_pdf_page_count_and_validate(b"")
    assert exc_info.value.code == ErrorCode.CORRUPT_FILE


@pytest.mark.asyncio
async def test_process_and_save_order_file(db_session, valid_pdf_6_pages, valid_pdf_20_pages):
    user = User(whatsapp_user_id="wa_file_test", phone_number="+916666666666")
    db_session.add(user)
    await db_session.flush()

    order = Order(order_code="ORD-FILES", user_id=user.id, status="CREATED")
    db_session.add(order)
    await db_session.flush()

    # Upload File 1
    f1 = await process_and_save_order_file(
        session=db_session,
        order=order,
        original_filename="resume.pdf",
        file_bytes=valid_pdf_6_pages,
    )
    assert f1.upload_sequence == 1
    assert f1.page_count == 6
    assert f1.original_filename == "resume.pdf"
    assert order.total_pages == 6

    # Upload File 2 with duplicate filename (assignment.pdf)
    f2 = await process_and_save_order_file(
        session=db_session,
        order=order,
        original_filename="resume.pdf",  # Duplicate name
        file_bytes=valid_pdf_20_pages,
    )
    assert f2.upload_sequence == 2
    assert f2.page_count == 20
    assert f2.id != f1.id  # Completely unique UUID identity
    assert order.total_pages == 26
