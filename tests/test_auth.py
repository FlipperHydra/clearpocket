import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD, Api


def test_register_sets_httponly_cookie_and_returns_csrf(app):
    api = Api(app)
    r = api.register("new@example.com", display_name="New")
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "new@example.com" and body["csrf_token"]
    assert "password" not in r.text and "hash" not in r.text
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "secure" not in cookie  # local HTTP development


def test_secure_cookie_configuration(settings, engine):
    from app.main import create_app
    from fastapi.testclient import TestClient

    secure = create_app(settings.with_overrides(cookie_secure=True, cookie_samesite="strict"))
    r = TestClient(secure).post("/api/auth/register", json={"email": "s@example.com", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("__host-clearpocket_session=") and "secure" in cookie and "samesite=strict" in cookie
    secure.state.engine.dispose()


def test_session_endpoint_and_logout(alice):
    s = alice.get("/api/auth/session").json()
    assert s["authenticated"] is True and s["user"]["email"] == "alice@example.com" and s["csrf_token"] == alice.csrf
    assert alice.post("/api/auth/logout").json() == {"ok": True}
    assert alice.get("/api/auth/session").json() == {"authenticated": False, "user": None, "csrf_token": None}
    assert alice.get("/api/months/2026-09").status_code == 401


def test_logout_invalidates_server_side_session(app, alice):
    name = app.state.settings.session_cookie_name
    stolen = alice.c.cookies.get(name)
    alice.post("/api/auth/logout")
    other = Api(app)
    other.c.cookies.set(name, stolen)
    assert other.get("/api/months/2026-09").status_code == 401


def test_login_works_and_issues_new_session(app, alice):
    name = app.state.settings.session_cookie_name
    first = alice.c.cookies.get(name)
    again = Api(app)
    assert again.login("ALICE@example.com").status_code == 200  # case-insensitive email
    assert again.c.cookies.get(name) != first
    assert again.get("/api/auth/session").json()["authenticated"] is True


def test_duplicate_email_conflict(alice, app):
    r = Api(app).register("Alice@Example.com")
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


def test_wrong_password_and_unknown_user_look_identical(alice, app):
    a = Api(app).login("alice@example.com", "wrong password here")
    b = Api(app).login("nobody@example.com", "wrong password here")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() and a.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.parametrize("payload", [
    {"email": "not-an-email", "password": PASSWORD},
    {"email": "a@example.com", "password": "Zq9!x"},
    {"email": "a@example.com", "password": "x" * 129},
    {"email": "a@example.com"},
    {"email": "a@example.com", "password": PASSWORD, "is_admin": True},
])
def test_registration_validation_never_echoes_password(app, payload):
    r = Api(app).c.post("/api/auth/register", json=payload)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert "Zq9!x" not in r.text and PASSWORD not in r.text


def test_password_and_session_token_are_not_stored_in_plaintext(app, alice, engine):
    with engine.connect() as c:
        pw = c.execute(text("SELECT password_hash FROM users")).scalar_one()
        th = c.execute(text("SELECT token_hash FROM sessions")).scalar_one()
    assert pw.startswith("$argon2id$") and PASSWORD not in pw
    assert th != alice.c.cookies.get(app.state.settings.session_cookie_name) and len(th) == 64


def test_login_rate_limit_per_account(app, alice):
    attacker = Api(app)
    for _ in range(5):
        assert attacker.login("alice@example.com", "wrong password here").status_code == 401
    blocked = attacker.login("alice@example.com", PASSWORD)  # even the right password is refused now
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"
    assert "retry-after" in blocked.headers


def test_successful_login_resets_account_failures(app, alice):
    c = Api(app)
    for _ in range(4):
        c.login("alice@example.com", "wrong password here")
    assert c.login("alice@example.com", PASSWORD).status_code == 200
    for _ in range(4):
        assert c.login("alice@example.com", "wrong password here").status_code == 401


def test_register_rate_limit(app):
    codes = [Api(app).register(f"u{i}@example.com").status_code for i in range(11)]
    assert codes[:10] == [201] * 10 and codes[10] == 429


def test_expired_session_is_rejected(alice, engine):
    with engine.begin() as c:
        c.execute(text("UPDATE sessions SET expires_at = now() - interval '1 second'"))
    assert alice.get("/api/auth/session").json()["authenticated"] is False
    assert alice.get("/api/transactions").status_code == 401


def test_absolute_lifetime_caps_session(alice, engine):
    with engine.begin() as c:
        c.execute(text("UPDATE sessions SET absolute_expires_at = now() - interval '1 second'"))
    assert alice.get("/api/transactions").status_code == 401


def test_csrf_required_for_state_changing_requests(alice):
    body = {"name": "Bills"}
    r = alice.c.post("/api/category-groups", json=body)  # no header
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = alice.c.post("/api/category-groups", json=body, headers={"X-CSRF-Token": "bogus"})
    assert r.status_code == 403
    assert alice.post("/api/category-groups", json=body).status_code == 201


def test_csrf_token_is_bound_to_the_session(alice, bob):
    r = alice.c.post("/api/category-groups", json={"name": "X"}, headers={"X-CSRF-Token": bob.csrf})
    assert r.status_code == 403


def test_logout_requires_csrf(alice):
    assert alice.c.post("/api/auth/logout").status_code == 403
    assert alice.get("/api/auth/session").json()["authenticated"] is True


def test_origin_checks(alice, app):
    good = {"Origin": "http://localhost:8000"}
    bad = {"Origin": "https://evil.example"}
    assert alice.post("/api/category-groups", json={"name": "A"}, headers=good).status_code == 201
    r = alice.post("/api/category-groups", json={"name": "B"}, headers=bad)
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    assert Api(app).c.post("/api/auth/login", json={"email": "a@example.com", "password": "x"}, headers=bad).status_code == 403
    assert alice.post("/api/category-groups", json={"name": "C"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_reads_do_not_need_csrf(alice):
    assert alice.c.get("/api/months/2026-09").status_code == 200
