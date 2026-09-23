import json
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.core.config import settings
from backend.models.configuration import PrintConfiguration
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.schemas.llm import PageRule
from backend.schemas.pricing import FilePricingDetail, PriceSnapshot
from backend.services.pricing.rules import validate_and_normalize_file_rules


def calculate_file_price(
    breakdown: dict[str, int],
    rates: dict[str, float] = None,
) -> float:
    if rates is None:
        rates = {
            "bw_single": settings.PRICE_BW_SINGLE,
            "bw_double": settings.PRICE_BW_DOUBLE,
            "color_single": settings.PRICE_COLOR_SINGLE,
            "color_double": settings.PRICE_COLOR_DOUBLE,
        }

    return (
        breakdown["bw_single"] * rates["bw_single"]
        + breakdown["bw_double"] * rates["bw_double"]
        + breakdown["color_single"] * rates["color_single"]
        + breakdown["color_double"] * rates["color_double"]
    )


async def calculate_order_pricing_snapshot(
    session: AsyncSession,
    order: Order,
) -> PriceSnapshot:
    """
    Pure deterministic price calculator.
    Formula:
      file_charge = number_of_files * base_charge
      printing_cost = sum(all page costs)
      total = file_charge + printing_cost
    """
    # Load files and configurations
    query = (
        select(OrderFile)
        .where(OrderFile.order_id == order.id, OrderFile.status != "DELETED")
        .options(selectinload(OrderFile.configuration))
        .order_by(OrderFile.upload_sequence)
    )
    res = await session.execute(query)
    files = res.scalars().all()

    file_count = len(files)
    file_base_charge = file_count * settings.PRICE_FILE_BASE_CHARGE
    total_pages = 0
    total_printing_charge = 0.0
    details: List[FilePricingDetail] = []

    for f in files:
        config = f.configuration
        color_mode = config.color_mode if config else "bw"
        default_sides = config.default_sides if config else "single"
        rules_raw = json.loads(config.rules_json) if config and config.rules_json else []
        rules = [PageRule(**r) for r in rules_raw]

        _, breakdown = validate_and_normalize_file_rules(
            page_count=f.page_count,
            file_color_mode=color_mode,
            file_default_sides=default_sides,
            custom_rules=rules,
        )

        file_cost = calculate_file_price(breakdown)
        total_pages += f.page_count
        total_printing_charge += file_cost

        details.append(
            FilePricingDetail(
                file_id=f.id,
                upload_sequence=f.upload_sequence,
                filename=f.original_filename,
                page_count=f.page_count,
                bw_single_pages=breakdown["bw_single"],
                bw_double_pages=breakdown["bw_double"],
                color_single_pages=breakdown["color_single"],
                color_double_pages=breakdown["color_double"],
                file_cost=file_cost,
            )
        )

    total_amount = round(file_base_charge + total_printing_charge, 2)

    snapshot = PriceSnapshot(
        file_count=file_count,
        file_base_charge=file_base_charge,
        total_pages=total_pages,
        printing_charge=round(total_printing_charge, 2),
        total_amount=total_amount,
        currency=settings.CURRENCY,
        pricing_version="v1",
        details=details,
    )

    # Persist updated totals on the order model
    order.total_pages = total_pages
    order.total_amount = total_amount
    order.price_snapshot = snapshot.model_dump_json()

    await session.flush()
    return snapshot
