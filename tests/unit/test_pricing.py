import json
import pytest
from backend.models.configuration import PrintConfiguration
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.models.user import User
from backend.schemas.llm import PageRule
from backend.services.pricing.engine import calculate_file_price, calculate_order_pricing_snapshot
from backend.services.pricing.rules import validate_and_normalize_file_rules


def test_validate_and_normalize_file_rules():
    # 20-page document: 1-4 single sided, 5-20 double sided
    rules = [
        PageRule(page_start=1, page_end=4, sides="single"),
        PageRule(page_start=5, page_end=20, sides="double"),
    ]
    norm_rules, breakdown = validate_and_normalize_file_rules(
        page_count=20,
        file_color_mode="bw",
        file_default_sides="single",
        custom_rules=rules,
    )

    assert breakdown["bw_single"] == 4
    assert breakdown["bw_double"] == 16
    assert breakdown["color_single"] == 0
    assert breakdown["color_double"] == 0

    cost = calculate_file_price(breakdown)
    # 4 * 2.0 (bw_single) + 16 * 3.0 (bw_double) = 8.0 + 48.0 = 56.0
    assert cost == 56.0


@pytest.mark.asyncio
async def test_order_pricing_snapshot_calculation(db_session):
    user = User(whatsapp_user_id="wa_pricing", phone_number="+917777777777")
    db_session.add(user)
    await db_session.flush()

    order = Order(order_code="ORD-PRICING", user_id=user.id, status="CREATED")
    db_session.add(order)
    await db_session.flush()

    # File 1: 6 pages BW single-sided (6 * 2.0 = 12.0)
    file1 = OrderFile(
        order_id=order.id,
        upload_sequence=1,
        original_filename="resume.pdf",
        file_hash="hash1",
        mime_type="application/pdf",
        storage_path="/tmp/resume.pdf",
        page_count=6,
        file_size=1024,
        status="VALIDATED",
    )
    db_session.add(file1)
    await db_session.flush()
    config1 = PrintConfiguration(
        order_id=order.id,
        file_id=file1.id,
        color_mode="bw",
        default_sides="single",
        rules_json="[]",
    )
    db_session.add(config1)

    # File 2: 20 pages BW (1-4 single, 5-20 double -> cost = 56.0)
    file2 = OrderFile(
        order_id=order.id,
        upload_sequence=2,
        original_filename="assignment.pdf",
        file_hash="hash2",
        mime_type="application/pdf",
        storage_path="/tmp/assignment.pdf",
        page_count=20,
        file_size=2048,
        status="VALIDATED",
    )
    db_session.add(file2)
    await db_session.flush()
    config2 = PrintConfiguration(
        order_id=order.id,
        file_id=file2.id,
        color_mode="bw",
        default_sides="double",
        rules_json=json.dumps([{"page_start": 1, "page_end": 4, "sides": "single"}]),
    )
    db_session.add(config2)
    await db_session.flush()

    snapshot = await calculate_order_pricing_snapshot(db_session, order)

    # Base charge: 2 files * 5.0 = 10.0
    # Printing charge: 12.0 + 56.0 = 68.0
    # Total = 10.0 + 68.0 = 78.0
    assert snapshot.file_count == 2
    assert snapshot.file_base_charge == 10.0
    assert snapshot.total_pages == 26
    assert snapshot.printing_charge == 68.0
    assert snapshot.total_amount == 78.0
    assert order.total_amount == 78.0
    assert order.total_pages == 26
