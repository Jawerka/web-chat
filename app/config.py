"""
Настройки приложения из переменных окружения.

Все значения по умолчанию заданы здесь; переопределение — через .env.
"""

from __future__ import annotations

import logging

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Центральный конфиг web-chat."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    web_host: str = "0.0.0.0"
    web_port: int = 8090
    mcp_port: int = 0  # 0 = web_port + 1
    public_base_url: str = "http://localhost:8090"
    # Опционально: URL при доступе через WireGuard (10.99.99.0/24)
    public_base_url_vpn: str = ""
    # Часовой пояс отображения в UI: Europe/Moscow или пусто = авто (браузер пользователя)
    display_timezone: str = ""

    llm_base_url: str = "http://192.168.88.41:8989/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_sec: int = 300

    sd_webui_url: str = "http://192.168.88.52:7860"
    sd_auth_user: str = ""
    sd_auth_pass: str = ""
    sd_negative_prompt: str = ""
    sd_steps: int = 22
    sd_sampler: str = "Euler a"
    sd_schedule_type: str = "Karras"
    sd_cfg_scale: float = 5.0
    sd_width: int = 1024
    sd_height: int = 1024
    request_timeout: int = 600
    mcp_timeout: int = 900

    database_url: str = "sqlite+aiosqlite:///./data/db/web_chat.sqlite"
    max_upload_mb: int = 25
    max_files_per_message: int = 10
    max_tool_rounds: int = 10
    max_history_messages: int = 60
    max_extract_chars: int = 50000

    # Vision: llama-server скачивает image_url по HTTP (лимит ~10 MB на стороне LLM)
    llm_vision_max_bytes: int = 6 * 1024 * 1024
    llm_vision_jpeg_quality: int = 88
    llm_vision_max_side_px: int = 4096

    upload_retention_days: int = 7
    generated_retention_days: int = 30

    @field_validator("public_base_url", "public_base_url_vpn")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        """Убрать завершающий слэш — URL картинок собираются явно."""
        return value.rstrip("/") if value else value

    @property
    def effective_mcp_port(self) -> int:
        """Порт MCP (streamable-http); по умолчанию web_port + 1."""
        return self.mcp_port if self.mcp_port > 0 else self.web_port + 1

    def validate_timeouts(self) -> None:
        """
        Проверить согласованность таймаутов MCP и SD.

        Если MCP_TIMEOUT <= REQUEST_TIMEOUT, логируется предупреждение
        (паттерн из image-gen validate_settings).
        """
        if self.mcp_timeout <= self.request_timeout:
            logger.warning(
                "MCP_TIMEOUT (%s) должен быть больше REQUEST_TIMEOUT (%s)",
                self.mcp_timeout,
                self.request_timeout,
            )


settings = Settings()
