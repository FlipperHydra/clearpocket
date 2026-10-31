import random
from datetime import date

from app.calculations import compute_timeline, min_ready_to_assign

SEP, OCT, NOV = date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)


def tl(cats, assigned=None, activity=None, income=None, through=SEP):
    return compute_timeline(cats, assigned or {}, activity or {}, income or {}, through)


def test_single_month_ready_to_assign_and_available():
    r = tl(["a", "b"], {("a", SEP): 30000, ("b", SEP): 10000}, {("a", SEP): -12000}, {SEP: 50000})[SEP]
    assert r.ready_to_assign == 10000
    assert r.categories["a"].available == 18000
    assert r.categories["b"].available == 10000
    assert (r.assigned, r.activity, r.income) == (40000, -12000, 50000)


def test_every_dollar_has_a_job_is_zero():
    assert tl(["a"], {("a", SEP): 5000}, {}, {SEP: 5000})[SEP].ready_to_assign == 0


def test_positive_balance_carries_forward_and_ready_to_assign_rolls():
    t = tl(["a"], {("a", SEP): 30000}, {("a", SEP): -10000}, {SEP: 50000}, through=NOV)
    assert t[OCT].categories["a"].carry_in == 20000
    assert t[NOV].categories["a"].available == 20000
    assert t[SEP].ready_to_assign == 20000 and t[OCT].ready_to_assign == 20000


def test_overspending_does_not_carry_in_category_but_reduces_next_ready_to_assign():
    t = tl(["a"], {("a", SEP): 10000}, {("a", SEP): -15000}, {SEP: 10000}, through=OCT)
    assert t[SEP].categories["a"].available == -5000
    assert t[SEP].overspent == 5000 and t[SEP].ready_to_assign == 0
    assert t[OCT].categories["a"].carry_in == 0 and t[OCT].categories["a"].available == 0
    assert t[OCT].ready_to_assign == -5000 and t[OCT].prior_overspent_absorbed == 5000


def test_new_month_assignment_and_activity_use_only_that_month():
    t = tl(["a"], {("a", SEP): 10000, ("a", OCT): 2000}, {("a", OCT): -500}, {SEP: 12000}, through=OCT)
    assert t[OCT].categories["a"].assigned == 2000
    assert t[OCT].categories["a"].available == 10000 + 2000 - 500
    assert t[OCT].ready_to_assign == 0  # 12000 income - 12000 assigned


def test_empty_future_month_inherits_and_gap_months_carry():
    t = tl(["a"], {("a", SEP): 100}, {}, {SEP: 100}, through=date(2027, 3, 1))
    assert t[date(2027, 3, 1)].categories["a"].available == 100
    assert len(t) == 7


def test_data_after_requested_month_is_ignored():
    t = tl(["a"], {("a", OCT): 999}, {}, {OCT: 999}, through=SEP)
    assert t[SEP].ready_to_assign == 0 and list(t) == [SEP]


def test_refund_is_positive_activity():
    r = tl(["a"], {("a", SEP): 1000}, {("a", SEP): -1000 + 300}, {SEP: 1000})[SEP]
    assert r.categories["a"].available == 300


def test_negative_assignment_from_transfer_of_carried_money():
    t = tl(["a", "b"], {("a", SEP): 10000, ("a", OCT): -4000, ("b", OCT): 4000}, {}, {SEP: 10000}, through=OCT)
    assert t[OCT].categories["a"].available == 6000
    assert t[OCT].categories["b"].available == 4000
    assert t[OCT].ready_to_assign == 0


def test_min_ready_to_assign_horizon():
    t = tl(["a"], {("a", SEP): 100}, {("a", SEP): -300}, {SEP: 100}, through=NOV)
    assert min_ready_to_assign(t, SEP) == -200
    assert min_ready_to_assign(t, NOV) == -200


def test_conservation_invariant_random():
    rng = random.Random(7)
    cats = ["a", "b", "c", "d"]
    months = [date(2026, m, 1) for m in range(1, 9)]
    for _ in range(300):
        assigned = {(c, m): rng.randint(-2000, 20000) for c in cats for m in months if rng.random() < 0.6}
        activity = {(c, m): rng.randint(-30000, 3000) for c in cats for m in months if rng.random() < 0.6}
        income = {m: rng.randint(1, 90000) for m in months if rng.random() < 0.7}
        t = compute_timeline(cats, assigned, activity, income, months[-1])
        cum_income = cum_activity = 0
        for m in months:
            cum_income += income.get(m, 0)
            cum_activity += sum(v for (c, mm), v in activity.items() if mm == m)
            r = t[m]
            assert r.ready_to_assign + r.available == cum_income + cum_activity
            assert all(cm.carry_in >= 0 for cm in r.categories.values())
