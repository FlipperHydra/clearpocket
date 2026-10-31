#!/usr/bin/env python3
"""End-to-end API verification for a RUNNING ClearPocket stack (standard library only).

  python3 scripts/smoke_test.py run     [--base http://localhost:8000] [--state .smoke-state.json] [--expire-cmd CMD] [--https]
  python3 scripts/smoke_test.py verify  [--base ...] [--state ...]     # after restart/recreate

`run` creates two throw-away users with random e-mails (never touches existing accounts).
Exit status is non-zero on the first failed check.
"""
from __future__ import annotations

import argparse, http.cookiejar, json, os, secrets, subprocess, urllib.error, urllib.request

PASSWORD = "Smoke-" + secrets.token_urlsafe(12)


def check(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        raise SystemExit(f"\nFAILED: {label}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)
        self.csrf = None

    def call(self, method, path, body=None, csrf=True, headers=None, raw=None):
        hdrs = {"Accept": "application/json", **(headers or {})}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        if csrf and self.csrf and method != "GET" and "X-CSRF-Token" not in hdrs:
            hdrs["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            resp = self.opener.open(req, timeout=20)
            status, text, rh = resp.status, resp.read().decode(), resp.headers
        except urllib.error.HTTPError as e:
            status, text, rh = e.code, e.read().decode(), e.headers
        try:
            payload = json.loads(text) if text else None
        except ValueError:
            payload = None
        return status, payload, rh, text

    def auth(self, kind, email, password=PASSWORD, **extra):
        s, p, h, t = self.call("POST", f"/api/auth/{kind}", {"email": email, "password": password, **extra}, csrf=False)
        if s in (200, 201):
            self.csrf = p["csrf_token"]
        return s, p, h, t

    def cookie_value(self):
        for c in self.jar:
            if "clearpocket_session" in c.name:
                return c.name, c.value
        return None, None


def err_code(p):
    return (p or {}).get("error", {}).get("code")


def month(c, m):
    s, p, *_ = c.call("GET", f"/api/months/{m}")
    check(s == 200, f"GET month {m} -> 200")
    return p


def cats_of(p):
    return {c["name"]: c for g in p["groups"] for c in g["categories"]}


def run(a):
    base = a.base
    anon = Client(base)
    ok = lambda s, want, label: check(s == want, f"{label} -> {want} (got {s})")

    print("== deployment surface ==")
    s, p, h, t = anon.call("GET", "/api/health"); ok(s, 200, "health")
    s, p, h, t = anon.call("GET", "/"); check(s == 200 and "ClearPocket" in t, "frontend served from same origin at /")
    for path in ("/styles.css", "/app.js"):
        s, *_ = anon.call("GET", path); ok(s, 200, f"static {path}")
    for path in ("/.env", "/backend/app/config.py", "/compose.yaml", "/Dockerfile", "/api/docs", "/api/openapi.json"):
        s, *_ = anon.call("GET", path); ok(s, 404, f"not exposed {path}")
    s, p, h, t = anon.call("GET", "/api/months/2026-09"); check(s == 401 and err_code(p) == "unauthenticated", "private endpoint without session -> 401")
    check(h.get("X-Content-Type-Options") == "nosniff", "security header X-Content-Type-Options")
    s, p, *_ = anon.call("GET", "/api/auth/session"); check(s == 200 and p["authenticated"] is False, "session endpoint signed-out shape")

    print("== registration, cookies, CSRF ==")
    email_a, email_b = f"smoke-a-{secrets.token_hex(4)}@example.com", f"smoke-b-{secrets.token_hex(4)}@example.com"
    A, B = Client(base), Client(base)
    s, p, h, t = A.auth("register", email_a, display_name="Smoke A")
    ok(s, 201, "register A")
    sc = h.get("Set-Cookie", "").lower()
    check("httponly" in sc and "samesite=" in sc and "path=/" in sc, "session cookie is HttpOnly + SameSite + Path=/")
    if a.https:
        check("secure" in sc and sc.startswith("__host-"), "cookie is Secure with __Host- prefix (HTTPS mode)")
    check(PASSWORD not in t and "hash" not in t, "register response leaks no password/hash")
    s, p, *_ = A.auth("register", email_a); check(s == 409 and err_code(p) == "email_taken", "duplicate e-mail -> 409")
    weak = "Zq9!x"
    s, p, h, t = Client(base).auth("register", f"w-{secrets.token_hex(3)}@example.com", weak)
    check(s == 422 and weak not in t, "weak password -> 422 and not echoed")
    s, p, *_ = A.call("POST", "/api/category-groups", {"name": "X"}, csrf=False); check(s == 403 and err_code(p) == "csrf_failed", "write without CSRF token -> 403")
    s, p, *_ = A.call("POST", "/api/category-groups", {"name": "X"}, headers={"X-CSRF-Token": "bogus"}); ok(s, 403, "write with wrong CSRF token")
    s, p, *_ = A.call("POST", "/api/category-groups", {"name": "X"}, headers={"Origin": "https://evil.example"}); check(s == 403 and err_code(p) == "bad_origin", "foreign Origin -> 403")
    s, p, *_ = A.call("GET", "/api/auth/session"); check(p["authenticated"] and p["user"]["email"] == email_a and p["csrf_token"] == A.csrf, "session endpoint returns user + same CSRF token")

    print("== categories, income, assignments ==")
    s, g, *_ = A.call("POST", "/api/category-groups", {"name": "Essentials"}); ok(s, 201, "create group")
    s, dup, *_ = A.call("POST", "/api/category-groups", {"name": "essentials"}); check(s == 409 and err_code(dup) == "duplicate_name", "duplicate group name (case-insensitive) -> 409")
    ids = {}
    for name in ("Rent", "Food", "Fun"):
        s, c, *_ = A.call("POST", "/api/categories", {"group_id": g["id"], "name": name, "target_text": f"{name} note"}); ok(s, 201, f"create category {name}")
        ids[name] = c["id"]
    s, p, *_ = A.call("POST", "/api/transactions", {"occurred_on": "2026-09-01", "payee": "Paycheck", "amount_cents": 100000}); ok(s, 201, "income transaction (no category)")
    m = month(A, "2026-09")
    check(m["summary"]["income_cents"] == 100000 and m["summary"]["ready_to_assign_cents"] == 100000, "ready to assign = income when nothing assigned")
    put = lambda cid, cents, ver, mo="2026-09": A.call("PUT", f"/api/months/{mo}/assignments/{cid}", {"assigned_cents": cents, "expected_version": ver})
    s, m, *_ = put(ids["Rent"], 60000, 0); ok(s, 200, "assign Rent 600.00")
    s, p, *_ = put(ids["Food"], 50000, 0); check(s == 422 and err_code(p) == "over_allocation", "over-allocation rejected (422)")
    s, m, *_ = put(ids["Food"], 30000, 0); ok(s, 200, "assign Food 300.00")
    check(m["summary"]["ready_to_assign_cents"] == 10000 and m["summary"]["assigned_cents"] == 90000, "totals: assigned 900.00, ready 100.00")
    s, p, *_ = put(ids["Food"], 20000, 0); check(s == 409 and err_code(p) == "version_conflict", "stale version -> 409 (no silent overwrite)")
    check(cats_of(month(A, "2026-09"))["Food"]["assigned_cents"] == 30000, "stale write did not change data")

    print("== transactions, transfers, overspending, months ==")
    s, t1, *_ = A.call("POST", "/api/transactions", {"occurred_on": "2026-09-10", "payee": "Corner Market", "amount_cents": -12000, "category_id": ids["Food"]}); ok(s, 201, "spend 120.00 on Food")
    c = cats_of(month(A, "2026-09"))
    check(c["Food"]["activity_cents"] == -12000 and c["Food"]["available_cents"] == 18000, "Food activity -120.00, available 180.00")
    s, m, *_ = A.call("POST", "/api/months/2026-09/moves", {"from_category_id": ids["Food"], "to_category_id": ids["Fun"], "amount_cents": 5000}); ok(s, 200, "move 50.00 Food -> Fun")
    c = cats_of(m)
    check(c["Food"]["available_cents"] == 13000 and c["Fun"]["available_cents"] == 5000 and m["summary"]["ready_to_assign_cents"] == 10000, "move updates both sides, ready unchanged")
    before = json.dumps(month(A, "2026-09"), sort_keys=True)
    s, p, *_ = A.call("POST", "/api/months/2026-09/moves", {"from_category_id": ids["Food"], "to_category_id": ids["Fun"], "amount_cents": 99999}); check(s == 422 and err_code(p) == "insufficient_funds", "move more than available -> 422")
    s, p, *_ = A.call("POST", "/api/months/2026-09/moves", {"from_category_id": ids["Food"], "to_category_id": ids["Food"], "amount_cents": 1}); ok(s, 422, "move to same category")
    check(json.dumps(month(A, "2026-09"), sort_keys=True) == before, "failed transfers changed nothing")
    s, *_ = A.call("POST", "/api/transactions", {"occurred_on": "2026-09-20", "payee": "Arcade", "amount_cents": -8000, "category_id": ids["Fun"]}); ok(s, 201, "overspend Fun by 30.00 (allowed)")
    m = month(A, "2026-09")
    check(cats_of(m)["Fun"]["available_cents"] == -3000 and m["summary"]["overspent_cents"] == 3000, "overspending shown as negative available")
    o = month(A, "2026-10")
    check(cats_of(o)["Food"]["carry_in_cents"] == 13000 and cats_of(o)["Food"]["available_cents"] == 13000, "positive balance carries into October")
    check(cats_of(o)["Fun"]["carry_in_cents"] == 0 and cats_of(o)["Fun"]["available_cents"] == 0, "overspent balance does NOT carry into category")
    check(o["summary"]["ready_to_assign_cents"] == 7000 and o["summary"]["prior_overspent_absorbed_cents"] == 3000, "overspend reduces October ready-to-assign (100.00 - 30.00)")
    s, p, *_ = A.call("GET", "/api/transactions?q=corner"); check(p["total"] == 1 and p["items"][0]["category_name"] == "Food", "search by payee")
    s, p, *_ = A.call("GET", "/api/transactions?q=ready"); check(p["total"] == 1 and p["items"][0]["payee"] == "Paycheck", "search 'ready' finds income")
    s, p, *_ = A.call("GET", "/api/transactions?q=%25"); check(p["total"] == 0, "'%' is literal in search")
    s, p, *_ = A.call("GET", "/api/transactions?month=2026-10"); check(p["total"] == 0, "month filter")
    s, p, *_ = A.call("DELETE", f"/api/categories/{ids['Food']}"); check(s == 409 and err_code(p) == "category_in_use", "category with history cannot be deleted")

    print("== validation and safe errors ==")
    base_tx = {"occurred_on": "2026-09-11", "payee": "x", "category_id": ids["Food"], "amount_cents": -5}
    for label, patch in (("float amount", {"amount_cents": 10.5}), ("string amount", {"amount_cents": "100"}), ("zero amount", {"amount_cents": 0}),
                         ("huge amount", {"amount_cents": 10**12}), ("bad date", {"occurred_on": "2026-13-40"}),
                         ("client-supplied user_id", {"user_id": "1"})):
        s, p, h, t = A.call("POST", "/api/transactions", {**base_tx, **patch}); check(s == 422 and err_code(p) == "validation_error", f"invalid input rejected: {label}")
    for bad in ("2026-13", "2026-9", "abc"):
        s, *_ = A.call("GET", f"/api/months/{bad}"); ok(s, 422, f"invalid month {bad}")
    s, p, h, t = A.call("POST", "/api/category-groups", raw=b"{not json"); check(s == 422 and "Traceback" not in t, "malformed JSON -> 422, no traceback")
    s, p, h, t = A.call("GET", "/api/nope"); check(s == 404 and err_code(p) == "not_found", "unknown route -> JSON 404")
    for needle in ("Traceback", "psycopg", "sqlalchemy", "SELECT "):
        check(needle not in t, f"error body leaks no '{needle}'")

    print("== two-user isolation (direct API attempts) ==")
    s, *_ = B.auth("register", email_b); ok(s, 201, "register B")
    check(B.call("GET", "/api/transactions")[1]["total"] == 0 and B.call("GET", "/api/category-groups")[1] == {"groups": []}, "B starts empty and sees none of A's data")
    mb = month(B, "2026-09")
    check(mb["groups"] == [] and mb["summary"]["income_cents"] == 0, "B's month is empty")
    ghost = "00000000-0000-4000-8000-0000000000aa"
    s, ghost_body, *_ = B.call("PUT", f"/api/months/2026-09/assignments/{ghost}", {"assigned_cents": 1, "expected_version": 0})
    attempts = [
        ("PUT", f"/api/months/2026-09/assignments/{ids['Food']}", {"assigned_cents": 1, "expected_version": 1}),
        ("POST", "/api/months/2026-09/moves", {"from_category_id": ids["Food"], "to_category_id": ids["Fun"], "amount_cents": 100}),
        ("POST", "/api/transactions", {"occurred_on": "2026-09-30", "payee": "Evil", "amount_cents": -100, "category_id": ids["Food"]}),
        ("DELETE", f"/api/transactions/{t1['id']}", None),
        ("PATCH", f"/api/categories/{ids['Food']}", {"name": "Hacked"}),
        ("DELETE", f"/api/categories/{ids['Food']}", None),
        ("PATCH", f"/api/category-groups/{g['id']}", {"name": "Hacked"}),
        ("DELETE", f"/api/category-groups/{g['id']}", None),
        ("POST", "/api/categories", {"group_id": g["id"], "name": "Sneaky"}),
    ]
    for method, path, body in attempts:
        s, p, *_ = B.call(method, path, body)
        check(s == 404 and err_code(p) == "not_found", f"B cannot {method} {path.split('/api/')[1][:34]} (404)")
    check(ghost_body is not None and err_code(ghost_body) == "not_found", "unknown IDs give the same 404 (no existence leak)")
    check(B.call("GET", f"/api/transactions?category_id={ids['Food']}")[1]["total"] == 0, "B cannot list A's category transactions")
    check(cats_of(month(A, "2026-09"))["Food"]["available_cents"] == 13000, "A's data unchanged after B's attempts")
    s, p, *_ = B.call("POST", "/api/category-groups", {"name": "mine", "user_id": "A"}); ok(s, 422, "B cannot pass a user_id")
    s, p, *_ = B.call("POST", "/api/sample-data"); ok(s, 201, "sample data is opt-in and affects only B")
    check(cats_of(month(A, "2026-09")).keys() == {"Rent", "Food", "Fun"}, "sample data for B did not touch A")

    print("== logout, replay, login, rate limit, expiry ==")
    name, value = A.cookie_value()
    s, p, *_ = A.call("POST", "/api/auth/logout", csrf=False); ok(s, 403, "logout without CSRF refused")
    s, *_ = A.call("POST", "/api/auth/logout"); ok(s, 200, "logout")
    replay = Client(base)
    s, p, *_ = replay.call("GET", "/api/months/2026-09", headers={"Cookie": f"{name}={value}"}); check(s == 401, "old session cookie is rejected after logout")
    s, *_ = A.auth("login", email_a, "wrong password here"); ok(s, 401, "wrong password")
    s2, p2, *_ = Client(base).auth("login", "nobody-" + secrets.token_hex(3) + "@example.com", "wrong password here")
    check(s2 == 401 and err_code(p2) == "invalid_credentials", "unknown user gets the same generic 401")
    s, *_ = A.auth("login", email_a); ok(s, 200, "login A")
    check(cats_of(month(A, "2026-09"))["Food"]["available_cents"] == 13000, "A's data is there after logout/login")
    victim = f"smoke-rl-{secrets.token_hex(4)}@example.com"
    codes = [Client(base).auth("login", victim, "wrong password here")[0] for _ in range(7)]
    check(codes[:5] == [401] * 5 and 429 in codes[5:], f"rate limit kicks in after 5 failures {codes}")
    s, p, h, t = Client(base).auth("login", victim, "wrong password here"); check(s == 429 and h.get("Retry-After"), "429 carries Retry-After")
    if a.expire_cmd:
        subprocess.run(a.expire_cmd, shell=True, check=True)
        s, p, *_ = A.call("GET", "/api/auth/session"); check(p["authenticated"] is False, "expired session -> signed-out")
        s, p, *_ = A.call("GET", "/api/transactions"); check(s == 401 and err_code(p) == "unauthenticated", "expired session -> 401 on private data")
        s, *_ = A.auth("login", email_a); ok(s, 200, "login again after expiry")
    else:
        print("SKIP  session expiry (pass --expire-cmd to test)")

    state = {"base": base, "password": PASSWORD, "a": {"email": email_a, "food_available": 13000, "oct_ready": 7000}, "b": {"email": email_b}}
    json.dump(state, open(a.state, "w"))
    print(f"\nALL CHECKS PASSED. State saved to {a.state}")


def verify(a):
    st = json.load(open(a.state))
    base = a.base or st["base"]
    A, B = Client(base), Client(base)
    s, *_ = A.auth("login", st["a"]["email"], st["password"]); check(s == 200, "A can still log in")
    c = cats_of(month(A, "2026-09"))
    check(set(c) == {"Rent", "Food", "Fun"} and c["Food"]["available_cents"] == st["a"]["food_available"], "A's categories and balances survived")
    check(month(A, "2026-10")["summary"]["ready_to_assign_cents"] == st["a"]["oct_ready"], "A's October carry-forward totals survived")
    check(A.call("GET", "/api/transactions")[1]["total"] == 3, "A's 3 transactions survived (nothing re-seeded)")
    s, *_ = B.auth("login", st["b"]["email"], st["password"]); check(s == 200, "B can still log in")
    mb = cats_of(month(B, "2026-09"))
    check("Rent" in mb and "Groceries" in mb and "Food" not in mb, "B's own opt-in sample data survived, separate from A's")
    print("\nPERSISTENCE VERIFIED.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run", "verify"])
    ap.add_argument("--base", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    ap.add_argument("--state", default=".smoke-state.json")
    ap.add_argument("--https", action="store_true")
    ap.add_argument("--expire-cmd", default=None)
    args = ap.parse_args()
    (run if args.mode == "run" else verify)(args)
