import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.api.dependencies import verify_agent_token
from backend.core.database import get_db
from backend.models.agent import AgentHeartbeat
from backend.models.queue import QueueEntry
from backend.schemas.agent import (
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    JobClaimRequest,
    JobClaimResponse,
    JobStatusUpdateRequest,
)
from backend.services.queue.service import claim_next_job, update_job_status

router = APIRouter(prefix="/agent", tags=["Print Agent"])


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
async def agent_heartbeat(
    payload: AgentHeartbeatRequest,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(verify_agent_token),
):
    query = select(AgentHeartbeat).where(AgentHeartbeat.agent_id == payload.agent_id)
    res = await session.execute(query)
    hb = res.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if not hb:
        hb = AgentHeartbeat(
            agent_id=payload.agent_id,
            status=payload.status,
            printer_status=payload.printer_status,
            last_seen_at=now,
            metadata_json=json.dumps(payload.metadata or {}),
        )
        session.add(hb)
    else:
        hb.status = payload.status
        hb.printer_status = payload.printer_status
        hb.last_seen_at = now
        hb.metadata_json = json.dumps(payload.metadata or {})

    # Count pending queue items
    q_count = await session.execute(
        select(func.count(QueueEntry.id)).where(QueueEntry.status == "WAITING")
    )
    pending_count = q_count.scalar_one() or 0

    await session.commit()
    return AgentHeartbeatResponse(
        acknowledged=True,
        server_time=now,
        pending_queue_count=pending_count,
    )


@router.post("/claim-job", response_model=JobClaimResponse)
async def claim_job_endpoint(
    payload: JobClaimRequest,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(verify_agent_token),
):
    res = await claim_next_job(session=session, agent_id=payload.agent_id)
    await session.commit()
    return res


@router.post("/jobs/{job_id}/status")
async def update_job_status_endpoint(
    job_id: str,
    payload: JobStatusUpdateRequest,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(verify_agent_token),
):
    job = await update_job_status(
        session=session,
        job_id=job_id,
        agent_id=payload.agent_id,
        status=payload.status,
        error_code=payload.error_code,
        error_message=payload.error_message,
    )
    await session.commit()
    return {
        "job_id": job.id,
        "status": job.status,
        "order_status": job.order.status,
    }
