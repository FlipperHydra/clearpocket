"""Pure unit tests (no database)."""
import pytest

from app.config import ConfigError, Settings, load_settings, normalize_database_url

S = "x" * 40
DB = "postgresql+psycopg://u:realpw@db/d"


def make(**kw):
    return Settings(database_url=DB, secret_key=S, **kw).validate()


def test_database_url_normalised():
    assert normalize_database_url("postgresql://u:p@h/d").startswith("postgresql+psycopg://")
    assert normalize_database_url("postgres://u:p@h/d").startswith("postgresql+psycopg://")


def test_production_requires_secure_cookie_and_https_origin():
    with pytest.raises(ConfigError):
        make(environment="production", cookie_secure=False, public_origin="https://b.example", allowed_origins=("https://b.example",))
    with pytest.raises(ConfigError):
        make(environment="production", cookie_secure=True)  # PUBLIC_ORIGIN missing
    make(environment="production", cookie_secure=True, public_origin="https://b.example", allowed_origins=("https://b.example",))


def test_short_or_placeholder_secrets_rejected_in_every_environment():
    with pytest.raises(ConfigError):
        Settings(database_url=DB, secret_key="short").validate()
    with pytest.raises(ConfigError):
        Settings(database_url=DB, secret_key="CHANGE_ME_" + "a" * 40, environment="local").validate()
    with pytest.raises(ConfigError):
        Settings(database_url="postgresql://u:CHANGE_ME_x@db/d", secret_key=S, environment="local").validate()


def test_https_origin_without_secure_cookie_is_refused():
    with pytest.raises(ConfigError):
        make(public_origin="https://b.example", allowed_origins=("https://b.example",), cookie_secure=False)


def test_secure_cookie_on_plain_http_lan_address_is_refused_but_localhost_is_ok():
    with pytest.raises(ConfigError):
        make(public_origin="http://192.168.1.20:8000", allowed_origins=("http://192.168.1.20:8000",), cookie_secure=True)
    make(public_origin="http://localhost:8000", allowed_origins=("http://localhost:8000",), cookie_secure=True)


def test_public_origin_must_be_allowed_and_well_formed():
    with pytest.raises(ConfigError):
        make(public_origin="http://localhost:8000", allowed_origins=("http://other",))
    with pytest.raises(ConfigError):
        make(public_origin="http://localhost:8000/app", allowed_origins=("http://localhost:8000/app",))


def test_cookie_name_and_warning():
    secure = make(cookie_secure=True)
    assert secure.session_cookie_name.startswith("__Host-") and secure.warnings() == []
    plain = make()
    assert not plain.session_cookie_name.startswith("__Host-") and plain.warnings()


def _env(monkeypatch, **kv):
    for k in ("PUBLIC_ORIGIN", "ALLOWED_ORIGINS", "COOKIE_SECURE", "ENVIRONMENT", "API_DOCS", "DATABASE_URL", "SECRET_KEY", "FRONTEND_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:realpw@db/d")
    monkeypatch.setenv("SECRET_KEY", S)
    for k, v in kv.items():
        monkeypatch.setenv(k, v)


def test_compose_style_env_with_empty_allowed_origins_uses_public_origin_and_twin(monkeypatch):
    _env(monkeypatch, PUBLIC_ORIGIN="http://localhost:8123", ALLOWED_ORIGINS="", COOKIE_SECURE="false", ENVIRONMENT="local")
    s = load_settings()
    assert s.allowed_origins == ("http://localhost:8123", "http://127.0.0.1:8123")
    assert s.api_docs is False and s.database_url.startswith("postgresql+psycopg://")


def test_https_deployment_env(monkeypatch):
    _env(monkeypatch, PUBLIC_ORIGIN="https://budget.example.com/", COOKIE_SECURE="true", ENVIRONMENT="production")
    s = load_settings()
    assert s.allowed_origins == ("https://budget.example.com",) and s.session_cookie_name.startswith("__Host-")


def test_explicit_allowed_origins_win(monkeypatch):
    _env(monkeypatch, PUBLIC_ORIGIN="http://localhost:8000", ALLOWED_ORIGINS="http://localhost:8000, http://nas.lan:8000")
    assert load_settings().allowed_origins == ("http://localhost:8000", "http://nas.lan:8000")
