import threading
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.services import budget as svc
from tests.conftest import cats, load_sample, month_of


def sums(api, month="2026-09"):
    m = month_of(api, month)
    return {c["name"]: (c["assigned_cents"], c["available_cents"]) for g in m["groups"] for c in g["categories"]}, m["summary"]


def move(api, a, b, amount, month="2026-09"):
    c = cats(api, month)
    return api.post(f"/api/months/{month}/moves", json={"from_category_id": c[a]["id"], "to_category_id": c[b]["id"], "amount_cents": amount})


def test_move_updates_both_sides_and_keeps_ready_to_assign(alice):
    load_sample(alice)
    before, s0 = sums(alice)
    r = move(alice, "Groceries", "Dining out", 2000)
    assert r.status_code == 200
    after, s1 = sums(alice)
    assert after["Groceries"][0] == before["Groceries"][0] - 2000 and after["Groceries"][1] == before["Groceries"][1] - 2000
    assert after["Dining out"][0] == before["Dining out"][0] + 2000 and after["Dining out"][1] == before["Dining out"][1] + 2000
    assert s1["ready_to_assign_cents"] == s0["ready_to_assign_cents"] == 0
    assert s1["assigned_cents"] == s0["assigned_cents"]
    assert r.json()["summary"]["assigned_cents"] == s1["assigned_cents"]


def test_move_whole_available_balance_is_allowed_but_one_cent_more_is_not(alice):
    load_sample(alice)
    avail = cats(alice)["Groceries"]["available_cents"]
    r = move(alice, "Groceries", "Dining out", avail + 1)
    assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_funds"
    assert r.json()["error"]["details"]["available_cents"] == avail
    assert move(alice, "Groceries", "Dining out", avail).status_code == 200
    assert cats(alice)["Groceries"]["available_cents"] == 0


def test_move_rejects_bad_requests_without_changes(alice):
    load_sample(alice)
    before = sums(alice)
    c = cats(alice)
    g, d = c["Groceries"]["id"], c["Dining out"]["id"]
    for body in (
        {"from_category_id": g, "to_category_id": g, "amount_cents": 100},
        {"from_category_id": g, "to_category_id": d, "amount_cents": 0},
        {"from_category_id": g, "to_category_id": d, "amount_cents": -5},
        {"from_category_id": g, "to_category_id": d, "amount_cents": 1.5},
    ):
        assert alice.post("/api/months/2026-09/moves", json=body).status_code == 422
    ghost = "00000000-0000-4000-8000-000000000009"
    assert alice.post("/api/months/2026-09/moves", json={"from_category_id": g, "to_category_id": ghost, "amount_cents": 1}).status_code == 404
    assert sums(alice) == before


def test_move_from_carried_over_money_in_a_later_month(alice):
    load_sample(alice)
    assert move(alice, "Groceries", "Dining out", 5000, month="2026-10").status_code == 200
    oct_ = cats(alice, "2026-10")
    assert oct_["Groceries"]["assigned_cents"] == -5000 and oct_["Groceries"]["available_cents"] == 11533 - 5000
    assert oct_["Dining out"]["available_cents"] == 10682 + 5000
    assert cats(alice, "2026-09")["Groceries"]["assigned_cents"] == 50000
    assert month_of(alice, "2026-10")["summary"]["ready_to_assign_cents"] == 0


def test_move_rolls_back_when_second_write_fails(make_api, monkeypatch):
    api = make_api("rb@example.com", raise_server_exceptions=False)
    load_sample(api)
    before = sums(api)
    c = cats(api)
    real, calls = svc._add_assigned, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash between the two writes")
        return real(*a, **k)

    monkeypatch.setattr(svc, "_add_assigned", flaky)
    r = api.post("/api/months/2026-09/moves", json={"from_category_id": c["Groceries"]["id"], "to_category_id": c["Dining out"]["id"], "amount_cents": 1000})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert calls["n"] == 2  # the first (debit) write really ran
    monkeypatch.undo()
    assert sums(api) == before  # ...and was rolled back


def test_database_check_constraint_failure_rolls_back_transfer(alice, monkeypatch):
    load_sample(alice)
    before = sums(alice)
    c = cats(alice)
    real, state = svc._add_assigned, {"n": 0}

    def bad_second_write(db, budget_id, month_id, category_id, delta):
        state["n"] += 1
        if state["n"] == 2:
            delta = 10**12  # violates the allocations assigned_cents CHECK constraint
        return real(db, budget_id, month_id, category_id, delta)

    monkeypatch.setattr(svc, "_add_assigned", bad_second_write)
    try:
        alice.post("/api/months/2026-09/moves", json={"from_category_id": c["Groceries"]["id"], "to_category_id": c["Dining out"]["id"], "amount_cents": 1000})
        raised = False
    except Exception:
        raised = True
    monkeypatch.undo()
    assert raised
    assert sums(alice) == before


def _budget_id(engine, api):
    uid = api.get("/api/auth/session").json()["user"]["id"]
    with engine.connect() as c:
        return str(c.execute(text("SELECT id FROM budgets WHERE user_id = :u"), {"u": uid}).scalar_one())


def _race(engine, workers):
    barrier, results = threading.Barrier(len(workers)), []

    def run(fn):
        with Session(engine) as s:
            barrier.wait()
            try:
                fn(s)
                results.append("ok")
            except ApiError as exc:
                results.append(exc.code)

    threads = [threading.Thread(target=run, args=(fn,)) for fn in workers]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return sorted(results)


def test_concurrent_assignments_cannot_over_allocate(alice, engine):
    g = alice.post("/api/category-groups", json={"name": "G"}).json()
    a = alice.post("/api/categories", json={"group_id": g["id"], "name": "A"}).json()["id"]
    b = alice.post("/api/categories", json={"group_id": g["id"], "name": "B"}).json()["id"]
    alice.post("/api/transactions", json={"occurred_on": "2026-09-01", "payee": "Pay", "amount_cents": 10000})
    bid = _budget_id(engine, alice)
    out = _race(engine, [lambda s, cat=cat: svc.set_assignment(s, bid, date(2026, 9, 1), cat, 7000, 0) for cat in (a, b)])
    assert out == ["ok", "over_allocation"]
    assert month_of(alice)["summary"]["ready_to_assign_cents"] == 3000


def test_concurrent_edits_of_same_assignment_do_not_lose_updates(alice, engine):
    g = alice.post("/api/category-groups", json={"name": "G"}).json()
    a = alice.post("/api/categories", json={"group_id": g["id"], "name": "A"}).json()["id"]
    alice.post("/api/transactions", json={"occurred_on": "2026-09-01", "payee": "Pay", "amount_cents": 10000})
    bid = _budget_id(engine, alice)
    out = _race(engine, [lambda s, amt=amt: svc.set_assignment(s, bid, date(2026, 9, 1), a, amt, 0) for amt in (1000, 2000)])
    assert out == ["ok", "version_conflict"]
    assert cats(alice)["A"]["assigned_cents"] in (1000, 2000)


def test_concurrent_moves_cannot_overdraw_the_source(alice, engine):
    load_sample(alice)
    bid = _budget_id(engine, alice)
    c = cats(alice)
    src, dst, avail = c["Groceries"]["id"], c["Dining out"]["id"], c["Groceries"]["available_cents"]
    out = _race(engine, [lambda s: svc.move_allocation(s, bid, date(2026, 9, 1), src, dst, avail) for _ in range(2)])
    assert out == ["insufficient_funds", "ok"]
    assert cats(alice)["Groceries"]["available_cents"] == 0
