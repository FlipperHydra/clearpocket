"""Pure unit tests need nothing. API tests need a PostgreSQL database named in TEST_DATABASE_URL
(its name must contain "test": the public schema is DROPPED and re-migrated)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSWORD = "correct horse battery"
SECRET = "test-secret-key-0123456789-abcdefghijklmnop"


def _db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; API tests need PostgreSQL (see README).")
    from app.config import normalize_database_url

    url = normalize_database_url(url)
    dbname = url.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in dbname:
        pytest.fail(f"Refusing to wipe database {dbname!r}: name must contain 'test'.")
    return url


@pytest.fixture(scope="session")
def db_url():
    return _db_url()


@pytest.fixture(scope="session")
def engine(db_url):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    eng = create_engine(db_url, future=True)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean(request):
    if "engine" not in request.fixturenames and "app" not in request.fixturenames:
        yield
        return
    eng = request.getfixturevalue("engine")
    from sqlalchemy import text

    with eng.begin() as c:
        c.execute(text("TRUNCATE users, auth_attempts RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def settings(db_url):
    from app.config import Settings

    return Settings(database_url=db_url, secret_key=SECRET, environment="test").validate()


@pytest.fixture
def app(settings, engine):
    from app.main import create_app

    application = create_app(settings)
    yield application
    application.state.engine.dispose()


class Api:
    """Thin wrapper holding one user's cookie jar and CSRF token."""

    def __init__(self, app, raise_server_exceptions=True):
        from fastapi.testclient import TestClient

        self.c = TestClient(app, raise_server_exceptions=raise_server_exceptions)
        self.csrf = None

    def _h(self, extra=None):
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        h.update(extra or {})
        return h

    def register(self, email="alice@example.com", password=PASSWORD, **kw):
        r = self.c.post("/api/auth/register", json={"email": email, "password": password, **kw})
        if r.status_code == 201:
            self.csrf = r.json()["csrf_token"]
        return r

    def login(self, email, password=PASSWORD):
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, headers=None, **kw):
        return self.c.post(url, json=json, headers=self._h(headers), **kw)

    def put(self, url, json=None, headers=None, **kw):
        return self.c.put(url, json=json, headers=self._h(headers), **kw)

    def patch(self, url, json=None, headers=None, **kw):
        return self.c.patch(url, json=json, headers=self._h(headers), **kw)

    def delete(self, url, headers=None, **kw):
        return self.c.delete(url, headers=self._h(headers), **kw)


@pytest.fixture
def make_api(app):
    def _make(email, raise_server_exceptions=True):
        api = Api(app, raise_server_exceptions)
        assert api.register(email).status_code == 201
        return api

    return _make


@pytest.fixture
def alice(make_api):
    return make_api("alice@example.com")


@pytest.fixture
def bob(make_api):
    return make_api("bob@example.com")


def month_of(api, month="2026-09"):
    r = api.get(f"/api/months/{month}")
    assert r.status_code == 200, r.text
    return r.json()


def cats(api, month="2026-09"):
    return {c["name"]: c for g in month_of(api, month)["groups"] for c in g["categories"]}


def load_sample(api):
    r = api.post("/api/sample-data")
    assert r.status_code == 201, r.text
