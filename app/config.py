"""Environment-driven settings. Secrets are never hard-coded."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

try:  # optional convenience for local (non-Docker) development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

_PLACEHOLDER_MARKERS = ("change_me", "change-me", "changeme")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


class ConfigError(RuntimeError):
    pass


def normalize_database_url(url: str) -> str:
    """Force the psycopg (v3) SQLAlchemy driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _origin_with_twin(origin: str) -> tuple[str, ...]:
    """http://localhost:8000 also allows http://127.0.0.1:8000 (and vice versa)."""
    parts = urlparse(origin)
    host = parts.hostname or ""
    twin = {"localhost": "127.0.0.1", "127.0.0.1": "localhost"}.get(host)
    if not twin:
        return (origin,)
    port = f":{parts.port}" if parts.port else ""
    return (origin, f"{parts.scheme}://{twin}{port}")


def _has_marker(value: str) -> bool:
    return any(m in value.lower() for m in _PLACEHOLDER_MARKERS)


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str = field(repr=False)
    environment: str = "development"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    public_origin: str = ""
    allowed_origins: tuple[str, ...] = ("http://localhost:8000", "http://127.0.0.1:8000")
    session_idle_hours: int = 168
    session_absolute_days: int = 30
    api_docs: bool = False
    frontend_dir: Path = Path(__file__).resolve().parents[2]
    # Rate limits (see app/services/rate_limit.py)
    rate_window_seconds: int = 900
    login_account_failures: int = 5
    login_ip_attempts: int = 30
    register_ip_attempts: int = 10

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def session_cookie_name(self) -> str:
        # __Host- prefix: browser enforces Secure + Path=/ + no Domain.
        return "__Host-clearpocket_session" if self.cookie_secure else "clearpocket_session"

    def validate(self) -> "Settings":
        if len(self.secret_key) < 32:
            raise ConfigError("SECRET_KEY must be at least 32 characters.")
        if _has_marker(self.secret_key):
            raise ConfigError("SECRET_KEY is still a placeholder. Generate a real one (see .env.example).")
        if _has_marker(self.database_url):
            raise ConfigError("The database password is still a placeholder. Set POSTGRES_PASSWORD (see .env.example).")
        if self.cookie_samesite not in {"lax", "strict"}:
            raise ConfigError("COOKIE_SAMESITE must be 'lax' or 'strict'.")
        if self.is_production and not self.cookie_secure:
            raise ConfigError("COOKIE_SECURE must be true in production (HTTPS).")
        if not self.database_url:
            raise ConfigError("DATABASE_URL is required.")
        if self.is_production and not self.public_origin:
            raise ConfigError("PUBLIC_ORIGIN (https://...) is required in production.")
        if self.public_origin:
            parts = urlparse(self.public_origin)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.path not in {"", "/"}:
                raise ConfigError("PUBLIC_ORIGIN must look like https://host[:port] (no path).")
            if parts.scheme == "https" and not self.cookie_secure:
                raise ConfigError("PUBLIC_ORIGIN is https but COOKIE_SECURE is false: session cookies would lack the Secure flag.")
            if parts.scheme == "http" and self.cookie_secure and parts.hostname not in _LOCAL_HOSTS:
                raise ConfigError(
                    "COOKIE_SECURE=true with a plain-HTTP PUBLIC_ORIGIN: browsers drop Secure cookies over HTTP, "
                    "so login would silently fail. Use https:// (via a reverse proxy) or set COOKIE_SECURE=false for local HTTP testing."
                )
            if self.public_origin.rstrip("/") not in self.allowed_origins:
                raise ConfigError("ALLOWED_ORIGINS must include PUBLIC_ORIGIN.")
        return self

    def warnings(self) -> list[str]:
        out = []
        if not self.cookie_secure:
            out.append("Session cookies are NOT marked Secure (plain-HTTP mode). Use only on localhost/LAN; enable HTTPS for anything else.")
        return out

    def with_overrides(self, **kw) -> "Settings":
        return replace(self, **kw).validate()


def load_settings() -> Settings:
    public_origin = os.environ.get("PUBLIC_ORIGIN", "").strip().rstrip("/")
    raw_allowed = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if raw_allowed:
        origins = tuple(o.strip().rstrip("/") for o in raw_allowed.split(",") if o.strip())
    elif public_origin:
        origins = _origin_with_twin(public_origin)
    else:
        origins = ("http://localhost:8000", "http://127.0.0.1:8000")
    return Settings(
        database_url=normalize_database_url(os.environ.get("DATABASE_URL", "")),
        secret_key=os.environ.get("SECRET_KEY", ""),
        environment=os.environ.get("ENVIRONMENT", "development").strip().lower(),
        cookie_secure=_bool("COOKIE_SECURE", False),
        cookie_samesite=os.environ.get("COOKIE_SAMESITE", "lax").strip().lower(),
        public_origin=public_origin,
        allowed_origins=origins,
        session_idle_hours=int(os.environ.get("SESSION_IDLE_HOURS", "168") or "168"),
        session_absolute_days=int(os.environ.get("SESSION_ABSOLUTE_DAYS", "30") or "30"),
        api_docs=_bool("API_DOCS", False),
        frontend_dir=Path(os.environ.get("FRONTEND_DIR") or Path(__file__).resolve().parents[2]),
    ).validate()


@lru_cache
def get_settings() -> Settings:
    return load_settings()
