import pytest

from tests.conftest import cats, load_sample

TXN = {"occurred_on": "2026-09-10", "payee": "Shop", "amount_cents": -500}


@pytest.fixture
def ready(alice):
    load_sample(alice)
    return alice


def err(r, status=422, code="validation_error"):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    assert set(r.json()["error"]) <= {"code", "message", "details"}
    return r.json()["error"]


@pytest.mark.parametrize("amount", [10.5, "100", True, None, 0, 10**11 + 1, -(10**11) - 1])
def test_transaction_amount_must_be_nonzero_bounded_integer(ready, amount):
    cat = cats(ready)["Groceries"]["id"]
    err(ready.post("/api/transactions", json={**TXN, "amount_cents": amount, "category_id": cat}))


def test_income_must_be_positive(ready):
    err(ready.post("/api/transactions", json={**TXN, "amount_cents": -500}))
    assert ready.post("/api/transactions", json={**TXN, "amount_cents": 500}).status_code == 201


@pytest.mark.parametrize("d", ["2026-13-01", "2026-02-30", "2026-9-1", "20260910", "1999-12-31", "2101-01-01", "", "tomorrow", 20260910, None])
def test_transaction_dates(ready, d):
    err(ready.post("/api/transactions", json={**TXN, "occurred_on": d, "amount_cents": 500}))


@pytest.mark.parametrize("payee", ["", "   ", "x" * 121, "bad\x00name", "line\nbreak", None, 5])
def test_transaction_payee(ready, payee):
    err(ready.post("/api/transactions", json={**TXN, "amount_cents": 500, "payee": payee}))


def test_transaction_unknown_fields_and_ids(ready):
    err(ready.post("/api/transactions", json={**TXN, "amount_cents": 500, "id": "x"}))
    err(ready.post("/api/transactions", json={**TXN, "category_id": "not-a-uuid"}))
    err(ready.post("/api/transactions", json={**TXN, "category_id": "00000000-0000-4000-8000-000000000009"}), 404, "not_found")


@pytest.mark.parametrize("month", ["2026-13", "2026-9", "abc", "1999-01", "2101-01", "2026-09-01"])
def test_invalid_months(ready, month):
    err(ready.get(f"/api/months/{month}"))
    err(ready.get(f"/api/months/{month}/summary"))
    err(ready.get(f"/api/transactions?month={month}"))


@pytest.mark.parametrize("body", [
    {"assigned_cents": -1, "expected_version": 1},
    {"assigned_cents": 1.5, "expected_version": 1},
    {"assigned_cents": "100", "expected_version": 1},
    {"assigned_cents": 10**11 + 1, "expected_version": 1},
    {"assigned_cents": 100},
    {"assigned_cents": 100, "expected_version": -1},
    {"assigned_cents": 100, "expected_version": 1, "category_id": "x"},
])
def test_assignment_bodies(ready, body):
    cid = cats(ready)["Groceries"]["id"]
    err(ready.put(f"/api/months/2026-09/assignments/{cid}", json=body))


def test_malformed_path_ids_and_json(ready):
    err(ready.put("/api/months/2026-09/assignments/not-a-uuid", json={"assigned_cents": 1, "expected_version": 0}))
    r = ready.c.post("/api/category-groups", content=b"{not json", headers={"X-CSRF-Token": ready.csrf, "Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("params", ["limit=0", "limit=201", "limit=abc", "offset=-1", "q=" + "x" * 101, "category_id=zzz"])
def test_list_query_params(ready, params):
    err(ready.get(f"/api/transactions?{params}"))


@pytest.mark.parametrize("name", ["", "  ", "x" * 61, "a\tb"])
def test_group_and_category_names(ready, name):
    gid = cats(ready)["Groceries"]["group_id"]
    err(ready.post("/api/category-groups", json={"name": name}))
    err(ready.post("/api/categories", json={"group_id": gid, "name": name}))


def test_unknown_route_and_method_use_error_envelope(ready):
    assert ready.get("/api/nope").json()["error"]["code"] == "not_found"
    assert ready.c.patch("/api/months/2026-09").json()["error"]["code"] in {"method_not_allowed", "csrf_failed", "http_error"}


def test_errors_do_not_leak_internals(ready):
    r = ready.get("/api/transactions?category_id=zzz")
    for needle in ("Traceback", "sqlalchemy", "psycopg", "SELECT "):
        assert needle not in r.text
