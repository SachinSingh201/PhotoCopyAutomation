from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BACKEND_URL: str = "http://localhost:8000"
    PRINT_AGENT_TOKEN: str = "print_agent_secret_token_123"
    PRINT_AGENT_ID: str = "agent-local-01"
    HEARTBEAT_INTERVAL_SECONDS: int = 15
    POLL_INTERVAL_SECONDS: int = 5
    MOCK_PRINT_EXECUTION_TIME_SECONDS: int = 2


agent_settings = AgentSettings()
