"""
Настройки приложения из переменных окружения.

Все значения по умолчанию заданы здесь; переопределение — через .env.
"""

from __future__ import annotations

import logging

from ipaddress import ip_address
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic import model_validator
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
    # HTTP ReadTimeout для тяжелых txt2img/img2img запросов.
    # Дефолт 600с для медленных окружений иногда недостаточен.
    request_timeout: int = 1200
    mcp_timeout: int = 900

    database_url: str = "sqlite+aiosqlite:///./data/db/web_chat.sqlite"
    # Postgres (P2.1): postgresql+asyncpg://user:pass@host:5432/web_chat
    db_pool_size: int = 5
    db_max_overflow: int = 10
    max_upload_mb: int = 25
    # Максимум width×height для загружаемых изображений (защита от decompression bomb)
    max_upload_image_pixels: int = 16_777_216
    max_pdf_pages: int = 500
    extract_timeout_sec: int = 120
    # Параллельные потоки для SD / extract (P1.2)
    job_queue_workers: int = 2
    shutdown_drain_sec: float = 30.0
    # SD HTTP: retry и circuit breaker (BE-2)
    sd_http_retries: int = 2
    sd_circuit_breaker_threshold: int = 3
    sd_circuit_breaker_cooldown_sec: float = 60.0
    # production — усиленная проверка AUTH при старте (SEC-1)
    web_chat_env: str = ""
    # Сброс буфера стрима в БД при накоплении N байт (дополнение к debounce 350 ms)
    stream_flush_min_bytes: int = 2048
    max_files_per_message: int = 10
    max_tool_rounds: int = 10
    # Повтор одного SD-tool (generate_image/img2img/upscale) в одном ходе
    max_same_tool_per_turn: int = 3
    max_history_messages: int = 60
    max_extract_chars: int = 50000
    # Ф1: снимок каталога @alias в system prompt (macro_context=full)
    macro_context_full_max_chars: int = 12_000
    macro_context_full_max_macros: int = 80
    # Ф2: embeddings для semantic search по каталогу @alias
    embedding_model: str = ""
    macro_search_top_k: int = 5
    # P2.3: RAG по extracted_text документов беседы
    rag_enabled: bool = False
    rag_auto_inject: bool = False
    rag_chunk_chars: int = 1500
    rag_chunk_overlap: int = 200
    rag_search_top_k: int = 5
    rag_context_max_chars: int = 8000

    # Vision: llama-server скачивает image_url по HTTP (лимит ~10 MB на стороне LLM)
    llm_vision_max_bytes: int = 6 * 1024 * 1024
    llm_vision_jpeg_quality: int = 88
    llm_vision_max_side_px: int = 4096

    # Превью в UI: WebP в БД и на диске (legacy generated/thumbs)
    media_thumb_max_px: int = 512
    media_preview_max_px: int = 320
    media_thumb_webp_quality: int = 82
    media_preview_webp_quality: int = 72

    upload_retention_days: int = 7
    generated_retention_days: int = 30
    # Корзина бесед: окончательное удаление через N дней после soft delete
    trash_retention_days: int = 3
    # P2.4: удалять orphan-файлы в data/generated/ (не в БД по original_name), старше N часов
    orphan_generated_min_age_hours: float = 24.0
    orphan_media_min_age_hours: float = 24.0

    # Журнал: файл + уровень (консоль systemd/journal всегда дублируется)
    log_file: str = "logs/web-chat.log"
    log_level: str = "INFO"
    log_file_max_bytes: int = 10 * 1024 * 1024
    log_file_backup_count: int = 5
    # true — JSON в консоль/файл/буфер UI (P1.6)
    log_json: bool = False
    # Ожидание LLM при 503 Loading model (секунды, суммарно)
    llm_model_load_wait_sec: int = 120
    llm_model_load_retry_sec: float = 2.0

    # --- Доступ и лимиты (P0, см. BACKLOG.md / HANDBOOK §21) ---
    # Пустой API_ACCESS_KEY — без проверки (доверенная LAN)
    api_access_key: str = ""
    # Через запятую: http://192.168.88.44:8090 — пусто = не проверять Origin
    trusted_ws_origins: str = ""
    # IP reverse proxy, которым доверяем X-Forwarded-For (через запятую)
    trusted_proxy_ips: str = ""
    # Доп. IP/хосты внутренних сервисов; хосты LLM_BASE_URL, SD_WEBUI_URL, PUBLIC_BASE_URL — автоматически
    trusted_internal_ips: str = ""
    # 127.0.0.1 / ::1 — LLM на том же хосте, dev
    trusted_internal_allow_loopback: bool = True
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_sec: int = 60
    # P2.2: сессии и изоляция данных по пользователю
    auth_enabled: bool = False
    auth_secret: str = ""
    auth_session_max_age_sec: int = 60 * 60 * 24 * 7
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_bootstrap_admin_login: str = "admin"
    auth_bootstrap_admin_password: str = "admin"
    # Устаревший заголовок; только если auth_enabled=false
    multi_user_enabled: bool = False
    multi_user_allow_header_fallback: bool = False
    multi_user_max_conversations: int = 0
    multi_user_max_uploads_per_day: int = 0

    @field_validator("public_base_url", "public_base_url_vpn")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        """Убрать завершающий слэш — URL картинок собираются явно."""
        return value.rstrip("/") if value else value

    @field_validator("public_base_url", "public_base_url_vpn")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        """Схема http(s) и хост без loopback/metadata (кроме localhost для dev)."""
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"PUBLIC_BASE_URL: недопустимая схема {parsed.scheme!r}")
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError("PUBLIC_BASE_URL: не указан host")
        if host in ("localhost", "127.0.0.1"):
            return value.rstrip("/")
        try:
            addr = ip_address(host)
            if addr.is_loopback or addr.is_link_local or addr.is_reserved:
                raise ValueError(f"PUBLIC_BASE_URL: недопустимый host {host}")
        except ValueError as exc:
            if "does not appear to be an IPv4 or IPv6 address" not in str(exc):
                raise
        return value.rstrip("/")

    def trusted_ws_origins_list(self) -> list[str]:
        """Разрешённые Origin для WebSocket."""
        if not self.trusted_ws_origins.strip():
            return []
        return [o.strip().rstrip("/") for o in self.trusted_ws_origins.split(",") if o.strip()]

    def trusted_proxy_ip_set(self) -> frozenset[str]:
        """IP reverse proxy для X-Forwarded-For."""
        if not self.trusted_proxy_ips.strip():
            return frozenset()
        return frozenset(p.strip() for p in self.trusted_proxy_ips.split(",") if p.strip())

    @property
    def effective_mcp_port(self) -> int:
        """Порт MCP (streamable-http); по умолчанию web_port + 1."""
        return self.mcp_port if self.mcp_port > 0 else self.web_port + 1

    @model_validator(mode="after")
    def validate_auth_settings(self) -> Settings:
        if self.auth_enabled and len((self.auth_secret or "").strip()) < 32:
            raise ValueError(
                "AUTH_SECRET обязателен при AUTH_ENABLED=true (минимум 32 символа, "
                "случайная строка; не коммитить в git)",
            )
        self._validate_production_auth()
        return self

    def _validate_production_auth(self) -> None:
        """Отказ старта в production при слабом bootstrap-пароле (SEC-1)."""
        if self.web_chat_env.strip().lower() != "production":
            return
        if not self.auth_enabled:
            return
        weak = frozenset(
            {
                "",
                "admin",
                "password",
                "123456",
                "changeme",
                self.auth_bootstrap_admin_login.strip().lower(),
            },
        )
        pwd = self.auth_bootstrap_admin_password.strip().lower()
        if pwd in weak or len(self.auth_bootstrap_admin_password) < 12:
            raise ValueError(
                "WEB_CHAT_ENV=production: задайте надёжный AUTH_BOOTSTRAP_ADMIN_PASSWORD "
                "(≥12 символов, не admin/password/123456)",
            )

    @property
    def effective_multi_user(self) -> bool:
        """Изоляция бесед: явный multi-user или вход по сессии."""
        return self.multi_user_enabled or self.auth_enabled

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
