import json
import pytest
from backend.core.config import settings
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
    expected_cost = (
        breakdown["bw_single"] * settings.PRICE_BW_SINGLE
        + breakdown["bw_double"] * settings.PRICE_BW_DOUBLE
        + breakdown["color_single"] * settings.PRICE_COLOR_SINGLE
        + breakdown["color_double"] * settings.PRICE_COLOR_DOUBLE
    )
    assert cost == expected_cost


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

    expected_base = 2 * settings.PRICE_FILE_BASE_CHARGE
    file1_cost = 6 * settings.PRICE_BW_SINGLE
    file2_cost = 4 * settings.PRICE_BW_SINGLE + 16 * settings.PRICE_BW_DOUBLE
    expected_printing = file1_cost + file2_cost
    expected_total = expected_base + expected_printing

    assert snapshot.file_count == 2
    assert snapshot.file_base_charge == expected_base
    assert snapshot.total_pages == 26
    assert snapshot.printing_charge == expected_printing
    assert snapshot.total_amount == expected_total
    assert order.total_amount == expected_total
    assert order.total_pages == 26
