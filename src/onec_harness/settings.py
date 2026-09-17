from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    llm_provider: str = "gigachat"
    llm_model: str = "GigaChat-3-Ultra"
    llm_timeout_seconds: float = 120.0
    llm_max_tokens: int = 4096
    llm_api_key: str | None = None

    gigachat_credentials: str | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_base_url: str = "https://api.giga.chat/v1"
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_verify_ssl: bool = True
    gigachat_ca_bundle: Path | None = None

    openai_base_url: str = "https://api.openai.com/v1"
    deepseek_base_url: str = "https://api.deepseek.com"
    openai_compatible_base_url: str | None = None

    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"

    # Primary development infobase. The autonomous validation loop never loads
    # staged XML here; a disposable/test copy is configured separately below.
    onec_exe: Path | None = None
    onec_ib_connection: str = ""
    onec_staging_ib_connection: str = ""
    onec_user: str | None = None
    onec_password: str | None = None
    onec_workspace: Path = Field(default_factory=lambda: Path("workspace"))
    onec_command_timeout_seconds: float = 600.0

    # External connection / runtime data access.
    onec_com_connection: str = ""
    onec_com_progid: str = "V83.COMConnector"
    onec_runtime_allow_writes: bool = False

    # MCP source mutations are a separate opt-in from runtime writes.
    onec_mcp_allow_writes: bool = False

    # Standard 1C automated testing. A Test Client must target a disposable
    # infobase explicitly or fall back to the staging infobase, never primary.
    onec_test_client_connection: str = ""
    onec_test_manager_connection: str = ""
    onec_test_manager_user: str | None = None
    onec_test_manager_password: str | None = None
    onec_test_host: str = "localhost"
    onec_test_port: int = 1538
    onec_test_client_id: str | None = None
    onec_test_startup_timeout_seconds: float = 30.0
    onec_test_timeout_seconds: float = 120.0

    @property
    def provider_name(self) -> str:
        return self.llm_provider.strip().lower().replace("-", "_")
