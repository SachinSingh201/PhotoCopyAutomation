import asyncio
import os
from typing import Optional
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from backend.api.routes import admin, agent, health, orders, payments, queue, whatsapp
from backend.core.config import settings
from backend.core.database import Base, async_engine, AsyncSessionLocal
from backend.core.exceptions import AppException
from backend.core.logging import logger
# Ensure all models are imported so Base metadata is complete
import backend.models
from backend.services.orders.service import cleanup_expired_orders
from backend.services.scheduling.service import seed_default_schedule_if_empty
from backend.services.whatsapp.service import whatsapp_service

STATIC_DIR = Path(__file__).parent / "static"


async def periodic_order_cleanup_worker():
    """Background worker to automatically cancel and delete files for unpaid orders exceeding the 5-minute timeout."""
    while True:
        try:
            await asyncio.sleep(30)
            async with AsyncSessionLocal() as session:
                await cleanup_expired_orders(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error in periodic order cleanup worker: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database schemas...")
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schemas initialized.")

    # Seed default shop settings & schedule if empty
    async with AsyncSessionLocal() as session:
        await seed_default_schedule_if_empty(session)

    # Ensure OpenWA webhook is registered if provider is openwa
    if settings.WHATSAPP_PROVIDER == "openwa":
        asyncio.create_task(whatsapp_service.ensure_webhook_registered())

    # Start 5-minute unpaid order cleanup task
    cleanup_task = asyncio.create_task(periodic_order_cleanup_worker())

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    logger.info("Shutting down application...")
    await async_engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    description="WhatsApp Document Printing Automation API & Admin Command Center",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Middleware
allowed_origins = ["*"] if settings.ENVIRONMENT == "development" else [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    logger.warning(
        f"AppException: {exc.code.value} - {exc.message}",
        extra={"error_code": exc.code.value, "details": exc.details},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.code.value,
            "message": exc.message,
            "details": exc.details,
        },
    )


# Mount Static Files for Admin UI
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Include Routers under standard prefixes and /api/v1
app.include_router(health.router)
app.include_router(health.router, prefix="/api/v1")
app.include_router(whatsapp.router)
app.include_router(whatsapp.router, prefix="/api/v1")
app.include_router(orders.router)
app.include_router(orders.router, prefix="/api/v1")
app.include_router(payments.router)
app.include_router(payments.router, prefix="/api/v1")
app.include_router(queue.router)
app.include_router(queue.router, prefix="/api/v1")
app.include_router(agent.router)
app.include_router(agent.router, prefix="/api/v1")

# Admin router under /admin and /api/v1/admin
app.include_router(admin.router)
app.include_router(admin.router, prefix="/api/v1")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin/paymentSetup", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin/{tab}", response_class=HTMLResponse, include_in_schema=False)
async def serve_admin_dashboard(tab: Optional[str] = None):
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h2>Admin UI not found</h2>", status_code=404)


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/admin")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)

