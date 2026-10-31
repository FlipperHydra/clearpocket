import pytest

from tests.conftest import cats, load_sample, month_of

G = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("method,url,body", [
    ("get", "/api/months/2026-09", None),
    ("get", "/api/months/2026-09/summary", None),
    ("put", f"/api/months/2026-09/assignments/{G}", {"assigned_cents": 1, "expected_version": 0}),
    ("post", "/api/months/2026-09/moves", {"from_category_id": G, "to_category_id": G.replace("1", "2"), "amount_cents": 1}),
    ("get", "/api/category-groups", None),
    ("post", "/api/category-groups", {"name": "x"}),
    ("patch", f"/api/category-groups/{G}", {"name": "x"}),
    ("delete", f"/api/category-groups/{G}", None),
    ("post", "/api/categories", {"group_id": G, "name": "x"}),
    ("patch", f"/api/categories/{G}", {"name": "x"}),
    ("delete", f"/api/categories/{G}", None),
    ("get", "/api/transactions", None),
    ("post", "/api/transactions", {"occurred_on": "2026-09-01", "payee": "x", "amount_cents": 100}),
    ("delete", f"/api/transactions/{G}", None),
    ("post", "/api/sample-data", None),
    ("post", "/api/auth/logout", None),
])
def test_unauthenticated_requests_get_401(app, method, url, body):
    from tests.conftest import Api

    r = getattr(Api(app).c, method)(url, **({"json": body} if body is not None else {}))
    assert r.status_code == 401, (method, url, r.text)
    assert r.json()["error"]["code"] == "unauthenticated"


def test_users_cannot_see_each_others_data(alice, bob):
    load_sample(alice)
    assert alice.get("/api/transactions").json()["total"] == 12
    assert bob.get("/api/transactions").json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert bob.get("/api/category-groups").json() == {"groups": []}
    m = month_of(bob)
    assert m["groups"] == [] and m["summary"]["ready_to_assign_cents"] == 0 and m["summary"]["income_cents"] == 0
    assert bob.get("/api/transactions?q=Landlord").json()["total"] == 0


def test_users_cannot_modify_each_others_data(alice, bob):
    load_sample(alice)
    c = cats(alice)
    gid, groc, dining = c["Groceries"]["group_id"], c["Groceries"]["id"], c["Dining out"]["id"]
    txn_id = alice.get("/api/transactions").json()["items"][0]["id"]
    before = month_of(alice)

    assert bob.put(f"/api/months/2026-09/assignments/{groc}", json={"assigned_cents": 1, "expected_version": 1}).status_code == 404
    assert bob.post("/api/months/2026-09/moves", json={"from_category_id": groc, "to_category_id": dining, "amount_cents": 100}).status_code == 404
    assert bob.post("/api/transactions", json={"occurred_on": "2026-09-30", "payee": "Evil", "amount_cents": -100, "category_id": groc}).status_code == 404
    assert bob.delete(f"/api/transactions/{txn_id}").status_code == 404
    assert bob.patch(f"/api/categories/{groc}", json={"name": "Hacked"}).status_code == 404
    assert bob.delete(f"/api/categories/{groc}").status_code == 404
    assert bob.patch(f"/api/category-groups/{gid}", json={"name": "Hacked"}).status_code == 404
    assert bob.delete(f"/api/category-groups/{gid}").status_code == 404
    assert bob.post("/api/categories", json={"group_id": gid, "name": "Sneaky"}).status_code == 404
    assert bob.get(f"/api/transactions?category_id={groc}").json()["total"] == 0
    assert month_of(alice) == before


def test_client_supplied_identity_fields_are_rejected(alice, bob):
    uid = alice.get("/api/auth/session").json()["user"]["id"]
    assert bob.post("/api/category-groups", json={"name": "x", "user_id": uid}).status_code == 422
    r = bob.post("/api/transactions", json={"occurred_on": "2026-09-01", "payee": "x", "amount_cents": 5, "budget_id": uid})
    assert r.status_code == 422
    assert bob.post("/api/category-groups", headers={"X-User-Id": uid}, json={"name": "mine"}).status_code == 201
    assert alice.get("/api/category-groups").json() == {"groups": []}


def test_same_names_allowed_for_different_users(alice, bob):
    load_sample(alice)
    load_sample(bob)
    assert alice.get("/api/transactions").json()["total"] == bob.get("/api/transactions").json()["total"] == 12
    assert cats(alice)["Rent"]["id"] != cats(bob)["Rent"]["id"]
