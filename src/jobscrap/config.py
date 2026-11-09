"""Load environment configuration once and reject invalid settings before doing I/O."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Shared process configuration; secret fields are excluded from dataclass repr."""
    mongodb_uri: str = field(default="", repr=False)
    mongodb_database: str = "jobscrap"
    mongodb_timeout_ms: int = 5000
    resume_path: Path = Path("Pranav_Gaur_Resume.pdf")
    state_dir: Path = Path("var")
    log_level: str = "INFO"
    user_agent: str = "JobScrap/0.1"
    http_timeout: float = 20
    request_interval: float = 2
    http_attempts: int = 3
    max_response_bytes: int = 5_000_000
    matching_mode: str = "deterministic"
    ai_provider: str = "openai"
    ai_model: str = ""
    ai_max_calls: int = 20
    ai_prefilter_min: float = 15
    ai_timeout: float = 30
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    dashboard_username: str = ""
    dashboard_password: str = field(default="", repr=False)

    def __post_init__(self):
        # Fail early rather than discovering an invalid budget, timeout or public
        # dashboard binding halfway through a scheduled scrape.
        if self.matching_mode not in {"deterministic", "hybrid"}:
            raise ValueError("MATCHING_MODE must be deterministic or hybrid")
        if self.matching_mode == "hybrid" and not self.ai_model:
            raise ValueError("AI_MODEL is required for hybrid matching")
        if bool(self.dashboard_username) != bool(self.dashboard_password):
            raise ValueError("Set both DASHBOARD_USERNAME and DASHBOARD_PASSWORD")
        if self.dashboard_host not in {"127.0.0.1", "localhost", "::1"} and not self.dashboard_password:
            raise ValueError("Dashboard authentication is required outside loopback")
        if min(self.http_timeout, self.ai_timeout, self.mongodb_timeout_ms) <= 0:
            raise ValueError("Timeouts must be positive")
        if not all(math.isfinite(value) for value in
                   (self.http_timeout, self.ai_timeout, self.request_interval, self.ai_prefilter_min)):
            raise ValueError("Timeouts, request intervals and thresholds must be finite")
        if self.http_attempts not in range(1, 6) or self.request_interval < 0:
            raise ValueError("HTTP_ATTEMPTS must be 1–5; request interval cannot be negative")
        if self.ai_max_calls < 0 or not 0 <= self.ai_prefilter_min <= 100:
            raise ValueError("Invalid AI call budget or prefilter score")
        if self.max_response_bytes <= 0 or not 1 <= self.dashboard_port <= 65535:
            raise ValueError("Invalid response limit or dashboard port")

    @classmethod
    def from_env(cls):
        """Respect shell overrides and resolve portable paths beside the selected .env."""
        from dotenv import load_dotenv

        env_path = Path(os.environ.get("JOBSCRAP_ENV_FILE", ".env")).resolve()
        load_dotenv(env_path, override=False)
        # A timer's working directory may differ from an interactive shell's.
        # Anchoring paths here keeps both processes on the same resume and lock.
        base = env_path.parent

        def path(key, default):
            value = Path(os.getenv(key, default)).expanduser()
            return value if value.is_absolute() else base / value

        return cls(
            mongodb_uri=os.getenv("MONGODB_URI", ""),
            mongodb_database=os.getenv("MONGODB_DATABASE", "jobscrap"),
            mongodb_timeout_ms=int(os.getenv("MONGODB_TIMEOUT_MS", "5000")),
            resume_path=path("RESUME_PATH", "Pranav_Gaur_Resume.pdf"),
            state_dir=path("STATE_DIR", "var"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            user_agent=os.getenv("SCRAPER_USER_AGENT", "JobScrap/0.1"),
            http_timeout=float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
            request_interval=float(os.getenv("REQUEST_INTERVAL_SECONDS", "2")),
            http_attempts=int(os.getenv("HTTP_ATTEMPTS", "3")),
            max_response_bytes=int(os.getenv("MAX_RESPONSE_BYTES", "5000000")),
            matching_mode=os.getenv("MATCHING_MODE", "deterministic"),
            ai_provider=os.getenv("AI_PROVIDER", "openai"),
            ai_model=os.getenv("AI_MODEL", ""),
            ai_max_calls=int(os.getenv("AI_MAX_CALLS_PER_RUN", "20")),
            ai_prefilter_min=float(os.getenv("AI_PREFILTER_MIN_SCORE", "15")),
            ai_timeout=float(os.getenv("AI_TIMEOUT_SECONDS", "30")),
            dashboard_host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("DASHBOARD_PORT", "8000")),
            dashboard_username=os.getenv("DASHBOARD_USERNAME", ""),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
        )
