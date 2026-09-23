import os
import pytest
import pytest_asyncio
import fitz  # PyMuPDF
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from backend.core.config import settings
from backend.core.database import Base, get_db
from backend.main import app
import backend.models

# Test database
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def create_sample_pdf(page_count: int = 1) -> bytes:
    """Helper to generate a valid PDF with N pages using PyMuPDF."""
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page(width=595, height=842)  # A4 size
        page.insert_text((50, 50), f"Sample Page {i + 1} for WhatsApp Document Printing")
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


@pytest.fixture
def valid_pdf_1_page():
    return create_sample_pdf(1)


@pytest.fixture
def valid_pdf_6_pages():
    return create_sample_pdf(6)


@pytest.fixture
def valid_pdf_20_pages():
    return create_sample_pdf(20)
