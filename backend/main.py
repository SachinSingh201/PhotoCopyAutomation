import os
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
from backend.services.scheduling.service import seed_default_schedule_if_empty

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database schemas...")
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schemas initialized.")

    # Seed default shop settings & schedule if empty
    async with AsyncSessionLocal() as session:
        await seed_default_schedule_if_empty(session)

    yield
    logger.info("Shutting down application...")
    await async_engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    description="WhatsApp Document Printing Automation API & Admin Command Center",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
async def serve_admin_dashboard():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h2>Admin UI not found</h2>", status_code=404)


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/admin")


# Include Routers under standard prefixes and /api/v1
app.include_router(health.router)
app.include_router(whatsapp.router)
app.include_router(orders.router)
app.include_router(payments.router)
app.include_router(queue.router)
app.include_router(agent.router)

# Admin router under /admin and /api/v1/admin
app.include_router(admin.router)
app.include_router(admin.router, prefix="/api/v1")
