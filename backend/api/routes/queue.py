from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.core.database import get_db
from backend.models.order import Order
from backend.models.queue import PrintJob, QueueEntry
from backend.schemas.queue import QueueItemRead

router = APIRouter(prefix="/queue", tags=["Queue"])


@router.get("", response_model=List[QueueItemRead])
async def list_active_queue(session: AsyncSession = Depends(get_db)):
    query = (
        select(PrintJob)
        .join(QueueEntry, QueueEntry.print_job_id == PrintJob.id)
        .join(Order, Order.id == PrintJob.order_id)
        .where(QueueEntry.status.in_(["WAITING", "PROCESSING"]))
        .order_by(QueueEntry.position.asc())
        .options(
            selectinload(PrintJob.order),
            selectinload(PrintJob.queue_entry),
        )
    )
    res = await session.execute(query)
    jobs = res.scalars().all()

    items = []
    for job in jobs:
        items.append(
            QueueItemRead(
                job_id=job.id,
                order_id=job.order.id,
                order_code=job.order.order_code,
                pickup_token=job.order.pickup_token,
                queue_position=job.queue_position,
                status=job.status,
                total_pages=job.order.total_pages,
                estimated_duration_seconds=job.estimated_duration_seconds,
                enqueued_at=job.queue_entry.enqueued_at if job.queue_entry else job.created_at,
            )
        )
    return items
