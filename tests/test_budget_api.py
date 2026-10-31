from tests.conftest import cats, load_sample, month_of


def summary(api, month="2026-09"):
    r = api.get(f"/api/months/{month}/summary")
    assert r.status_code == 200
    return r.json()


def test_new_user_starts_empty(alice):
    m = month_of(alice)
    assert m["groups"] == [] and m["summary"]["ready_to_assign_cents"] == 0
    assert alice.get("/api/transactions").json()["total"] == 0
    assert alice.get("/api/category-groups").json() == {"groups": []}


def test_sample_data_is_opt_in_and_matches_the_demo(alice):
    load_sample(alice)
    s = summary(alice)
    assert s["income_cents"] == 368500 and s["assigned_cents"] == 368500 and s["ready_to_assign_cents"] == 0
    assert s["activity_cents"] == -(145000 + 11842 + 6500 + 38467 + 7318 + 4218 + 2800 + 3850)
    c = cats(alice)
    assert c["Rent"]["available_cents"] == 0
    assert c["Utilities"]["activity_cents"] == -11842 and c["Utilities"]["available_cents"] == 14000 - 11842
    assert c["Groceries"]["activity_cents"] == -38467 and c["Groceries"]["available_cents"] == 11533
    assert c["Dining out"]["activity_cents"] == -7318
    assert c["Emergency fund"]["available_cents"] == 50000
    assert [g["name"] for g in month_of(alice)["groups"]] == ["Home & bills", "Everyday life", "Future plans"]


def test_sample_data_refused_when_budget_not_empty(alice):
    load_sample(alice)
    r = alice.post("/api/sample-data")
    assert r.status_code == 409 and r.json()["error"]["code"] == "budget_not_empty"
    assert alice.get("/api/transactions").json()["total"] == 12


def test_sample_data_failure_rolls_back_everything(make_api, monkeypatch):
    from app.services import sample

    api = make_api("fail@example.com", raise_server_exceptions=False)

    def boom(*a, **k):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(sample, "_insert_transactions", boom)
    r = api.post("/api/sample-data")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "simulated" not in r.text
    assert api.get("/api/category-groups").json() == {"groups": []}
    monkeypatch.undo()
    assert api.post("/api/sample-data").status_code == 201


def test_groups_and_categories_crud(alice):
    g = alice.post("/api/category-groups", json={"name": "Bills"}).json()
    assert alice.post("/api/category-groups", json={"name": "bills"}).status_code == 409
    c = alice.post("/api/categories", json={"group_id": g["id"], "name": "Rent", "target_text": "Due 1st"}).json()
    assert alice.post("/api/categories", json={"group_id": g["id"], "name": "RENT"}).status_code == 409
    p = alice.patch(f"/api/categories/{c['id']}", json={"name": "Rent & fees", "target_text": None}).json()
    assert p["name"] == "Rent & fees" and p["target_text"] is None
    assert alice.delete(f"/api/category-groups/{g['id']}").json()["error"]["code"] == "group_not_empty"
    assert alice.delete(f"/api/categories/{c['id']}").status_code == 204
    assert alice.delete(f"/api/category-groups/{g['id']}").status_code == 204
    assert alice.get("/api/category-groups").json() == {"groups": []}


def test_category_with_history_cannot_be_deleted(alice):
    load_sample(alice)
    rent = cats(alice)["Rent"]["id"]
    r = alice.delete(f"/api/categories/{rent}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "category_in_use"
    assert "Rent" in cats(alice)


def test_assignment_decrease_then_increase_with_versions(alice):
    load_sample(alice)
    groc = cats(alice)["Groceries"]
    assert groc["assignment_version"] == 1
    url = f"/api/months/2026-09/assignments/{groc['id']}"
    r = alice.put(url, json={"assigned_cents": 40000, "expected_version": 1})
    assert r.status_code == 200
    out = {c["name"]: c for g in r.json()["groups"] for c in g["categories"]}
    assert out["Groceries"]["assigned_cents"] == 40000 and out["Groceries"]["assignment_version"] == 2
    assert r.json()["summary"]["ready_to_assign_cents"] == 10000
    assert alice.put(url, json={"assigned_cents": 50000, "expected_version": 2}).status_code == 200
    assert summary(alice)["ready_to_assign_cents"] == 0


def test_stale_version_is_a_conflict_not_a_silent_overwrite(alice):
    load_sample(alice)
    groc = cats(alice)["Groceries"]
    url = f"/api/months/2026-09/assignments/{groc['id']}"
    assert alice.put(url, json={"assigned_cents": 45000, "expected_version": 1}).status_code == 200
    r = alice.put(url, json={"assigned_cents": 10000, "expected_version": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    assert r.json()["error"]["details"] == {"current_version": 2, "current_assigned_cents": 45000}
    assert cats(alice)["Groceries"]["assigned_cents"] == 45000


def test_over_allocation_is_rejected_with_details(alice):
    load_sample(alice)
    groc = cats(alice)["Groceries"]
    r = alice.put(f"/api/months/2026-09/assignments/{groc['id']}", json={"assigned_cents": 50001, "expected_version": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "over_allocation"
    assert r.json()["error"]["details"]["ready_to_assign_cents"] == 0
    assert cats(alice)["Groceries"]["assigned_cents"] == 50000


def test_first_assignment_uses_version_zero_and_income_unlocks_it(alice):
    g = alice.post("/api/category-groups", json={"name": "G"}).json()
    c = alice.post("/api/categories", json={"group_id": g["id"], "name": "Food"}).json()
    url = f"/api/months/2026-09/assignments/{c['id']}"
    assert alice.put(url, json={"assigned_cents": 100, "expected_version": 0}).json()["error"]["code"] == "over_allocation"
    assert alice.post("/api/transactions", json={"occurred_on": "2026-09-01", "payee": "Pay", "amount_cents": 20000}).status_code == 201
    assert summary(alice)["ready_to_assign_cents"] == 20000
    assert alice.put(url, json={"assigned_cents": 15000, "expected_version": 0}).status_code == 200
    assert summary(alice)["ready_to_assign_cents"] == 5000


def test_transactions_drive_activity_and_overspending_is_allowed(alice):
    load_sample(alice)
    groc = cats(alice)["Groceries"]["id"]
    r = alice.post("/api/transactions", json={"occurred_on": "2026-09-25", "payee": "Big shop", "amount_cents": -20000, "category_id": groc})
    assert r.status_code == 201 and r.json()["category_name"] == "Groceries"
    c = cats(alice)["Groceries"]
    assert c["activity_cents"] == -58467 and c["available_cents"] == 50000 - 58467
    s = summary(alice)
    assert s["overspent_cents"] == 8467 and s["ready_to_assign_cents"] == 0


def test_overspending_reduces_next_months_ready_to_assign_not_the_category(alice):
    load_sample(alice)
    groc = cats(alice)["Groceries"]["id"]
    alice.post("/api/transactions", json={"occurred_on": "2026-09-25", "payee": "Big shop", "amount_cents": -20000, "category_id": groc})
    oct_ = month_of(alice, "2026-10")
    g = {c["name"]: c for gr in oct_["groups"] for c in gr["categories"]}["Groceries"]
    assert g["carry_in_cents"] == 0 and g["available_cents"] == 0
    assert oct_["summary"]["ready_to_assign_cents"] == -8467 and oct_["summary"]["is_over_allocated"] is True
    assert oct_["summary"]["prior_overspent_absorbed_cents"] == 8467
    rent = cats(alice, "2026-10")["Rent"]["id"]
    r = alice.put(f"/api/months/2026-10/assignments/{rent}", json={"assigned_cents": 1, "expected_version": 0})
    assert r.status_code == 422 and r.json()["error"]["code"] == "over_allocation"


def test_months_carry_forward_and_can_be_planned_ahead(alice):
    load_sample(alice)
    oct_cats = cats(alice, "2026-10")
    assert oct_cats["Groceries"]["carry_in_cents"] == 11533 and oct_cats["Groceries"]["available_cents"] == 11533
    assert oct_cats["Groceries"]["assigned_cents"] == 0 and oct_cats["Groceries"]["activity_cents"] == 0
    assert oct_cats["Rent"]["available_cents"] == 0
    assert summary(alice, "2026-10")["ready_to_assign_cents"] == 0
    assert summary(alice, "2026-08")["income_cents"] == 0
    groc = cats(alice, "2026-10")["Groceries"]
    assert alice.post("/api/transactions", json={"occurred_on": "2026-10-02", "payee": "Oct pay", "amount_cents": 400000}).status_code == 201
    r = alice.put(f"/api/months/2026-10/assignments/{groc['id']}", json={"assigned_cents": 30000, "expected_version": 0})
    assert r.status_code == 200
    assert cats(alice, "2026-10")["Groceries"]["available_cents"] == 11533 + 30000
    assert summary(alice, "2026-10")["ready_to_assign_cents"] == 400000 - 30000
    assert summary(alice, "2026-11")["ready_to_assign_cents"] == 370000
    assert cats(alice, "2026-11")["Groceries"]["carry_in_cents"] == 41533


def test_increase_in_an_earlier_month_cannot_hide_a_later_shortfall(alice):
    load_sample(alice)
    groc = cats(alice)["Groceries"]["id"]
    rent = cats(alice)["Rent"]["id"]
    alice.post("/api/transactions", json={"occurred_on": "2026-10-02", "payee": "Oct pay", "amount_cents": 1000})
    alice.put(f"/api/months/2026-10/assignments/{groc}", json={"assigned_cents": 1000, "expected_version": 0})
    assert summary(alice, "2026-10")["ready_to_assign_cents"] == 0
    r = alice.put(f"/api/months/2026-09/assignments/{rent}", json={"assigned_cents": 145001, "expected_version": 1})
    assert r.json()["error"]["code"] == "over_allocation"


def test_transaction_listing_search_and_pagination(alice):
    load_sample(alice)
    all_ = alice.get("/api/transactions").json()
    assert all_["total"] == 12 and all_["items"][0]["payee"] == "Shell Station"
    dates = [t["occurred_on"] for t in all_["items"]]
    assert dates == sorted(dates, reverse=True)
    assert alice.get("/api/transactions?q=green basket").json()["total"] == 3
    assert alice.get("/api/transactions?q=DINING").json()["total"] == 2
    assert alice.get("/api/transactions?q=ready").json()["items"][0]["payee"] == "September income"
    assert alice.get("/api/transactions?q=100%25").json()["total"] == 0
    assert alice.get("/api/transactions?q=").json()["total"] == 12
    assert alice.get("/api/transactions?month=2026-10").json()["total"] == 0
    page = alice.get("/api/transactions?limit=5&offset=10").json()
    assert len(page["items"]) == 2 and page["total"] == 12
    income = [t for t in all_["items"] if t["category_id"] is None]
    assert income[0]["amount_cents"] == 368500 and income[0]["category_name"] is None


def test_delete_transaction_updates_activity(alice):
    load_sample(alice)
    t = alice.get("/api/transactions?q=Landlord").json()["items"][0]
    assert alice.delete(f"/api/transactions/{t['id']}").status_code == 204
    assert cats(alice)["Rent"]["available_cents"] == 145000
    assert alice.delete(f"/api/transactions/{t['id']}").status_code == 404
