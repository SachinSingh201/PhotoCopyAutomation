from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    APP_NAME: str = "WhatsApp-Print-System"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    SECRET_KEY: str = "dev-insecure-secret-key-change-in-production"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./whatsapp_print.db"
    DATABASE_SYNC_URL: str = "sqlite:///./whatsapp_print.db"

    # Storage
    STORAGE_ROOT: str = "./storage/private"

    # File Processing Constraints
    MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB
    MAX_FILES_PER_ORDER: int = 10
    ALLOWED_MIME_TYPES: list[str] = ["application/pdf"]

    # WhatsApp API
    WHATSAPP_PHONE_NUMBER_ID: str = "mock_phone_number_id"
    WHATSAPP_TOKEN: str = "mock_whatsapp_token"
    WHATSAPP_VERIFY_TOKEN: str = "mock_verify_token"
    WHATSAPP_API_VERSION: str = "v19.0"

    # LLM / NLP Service
    LLM_PROVIDER: str = "mock"  # "gemini" | "openai" | "mock"
    LLM_API_KEY: str = "mock_llm_key"
    LLM_MODEL: str = "gemini-1.5-flash"

    # Payment Gateway
    PAYMENT_PROVIDER: str = "mock"  # "razorpay" | "stripe" | "mock"
    PAYMENT_KEY_ID: str = "mock_key_id"
    PAYMENT_KEY_SECRET: str = "mock_key_secret"
    PAYMENT_WEBHOOK_SECRET: str = "mock_webhook_secret"

    # Pricing (in base currency unit, e.g. INR)
    PRICE_FILE_BASE_CHARGE: float = 5.0
    PRICE_BW_SINGLE: float = 2.0
    PRICE_BW_DOUBLE: float = 3.0
    PRICE_COLOR_SINGLE: float = 10.0
    PRICE_COLOR_DOUBLE: float = 15.0
    CURRENCY: str = "INR"

    # Print Agent
    PRINT_AGENT_TOKEN: str = "print_agent_secret_token_123"
    PRINT_AGENT_ID: str = "agent-local-01"
    PRINTER_SPEED_PPM: int = 20  # pages per minute for ETA calculation
    AGENT_HEARTBEAT_TIMEOUT_SECONDS: int = 45

    # Admin
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin_password_change_me"
    ADMIN_SECRET: str = "admin_secret_token_change_me"

    @property
    def storage_path(self) -> Path:
        p = Path(self.STORAGE_ROOT).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
