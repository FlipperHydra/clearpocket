# ClearPocket API contract (v1)

Same origin, prefix `/api`. JSON in/out. Money fields end in `_cents` (integers). Months are `YYYY-MM`, dates `YYYY-MM-DD`, IDs UUIDs.

## Authentication and CSRF
1. `POST /api/auth/register` or `/login` sets an HttpOnly session cookie (`clearpocket_session`, or `__Host-clearpocket_session` when `COOKIE_SECURE=true`) and returns `csrf_token`.
2. Browsers send the cookie automatically (`credentials: "same-origin"`).
3. Every `POST/PUT/PATCH/DELETE` on a private endpoint needs header `X-CSRF-Token`. After a reload call `GET /api/auth/session` to get it again.
4. Unsafe requests with an `Origin` not in the allowed origins get `403 bad_origin`.

## Errors
`{"error": {"code": "...", "message": "...", "details": ...}}`

| Status | code | Meaning |
|---|---|---|
| 401 | `unauthenticated`, `invalid_credentials` | No/expired session; bad login |
| 403 | `csrf_failed`, `bad_origin` | CSRF / origin refused |
| 404 | `not_found` | Missing or not yours |
| 409 | `email_taken`, `duplicate_name`, `version_conflict`, `group_not_empty`, `category_in_use`, `budget_not_empty` | Conflict |
| 422 | `validation_error`, `over_allocation`, `insufficient_funds` | Bad input / rule |
| 429 | `rate_limited` | `Retry-After` header |
| 500 | `internal_error` | Generic |

## Endpoints
| Endpoint | Body | Result |
|---|---|---|
| `POST /api/auth/register` | `{email, password(12-128), display_name?}` | 201 `{user, csrf_token}` |
| `POST /api/auth/login` | `{email, password}` | 200 `{user, csrf_token}` |
| `POST /api/auth/logout` (CSRF) | | `{ok:true}` |
| `GET /api/auth/session` | | `{authenticated, user, csrf_token}` (always 200) |
| `GET /api/months/{m}` | | `{month, summary, groups[{..., categories[{carry_in_cents, assigned_cents, activity_cents, available_cents, assignment_version}]}]}` |
| `GET /api/months/{m}/summary` | | summary (income, assigned, activity, available, carry_in, overspent, prior_overspent_absorbed, cumulative_*, ready_to_assign, is_over_allocated) |
| `PUT /api/months/{m}/assignments/{category_id}` (CSRF) | `{assigned_cents, expected_version}` | month payload; 409 `version_conflict`, 422 `over_allocation` |
| `POST /api/months/{m}/moves` (CSRF) | `{from_category_id, to_category_id, amount_cents}` | month payload; 422 `insufficient_funds` |
| `GET/POST /api/category-groups`, `PATCH/DELETE /api/category-groups/{id}` | `{name, sort_order?}` | groups; 409 `group_not_empty` |
| `POST /api/categories`, `PATCH/DELETE /api/categories/{id}` | `{group_id, name, target_text?}` | category; 409 `category_in_use` |
| `GET /api/transactions?month=&q=&category_id=&limit=&offset=` | | `{items, total, limit, offset}` |
| `POST /api/transactions` (CSRF) | `{occurred_on, payee, amount_cents, category_id?, memo?}` | 201 item. Negative = outflow; no category = income (must be positive) |
| `DELETE /api/transactions/{id}` (CSRF) | | 204 |
| `POST /api/sample-data` (CSRF) | | 201; 409 unless the budget is empty |
| `GET /api/health` | | `{status:"ok"}` (checks DB) |

## Financial rules
`available = carry_in + assigned + activity`; positive balances carry forward; negative ones do not and instead reduce the next month's `ready_to_assign`. Assignment increases that would make `ready_to_assign` negative (this or any later month) are rejected; decreases are allowed. Overspending is never rejected. Transfers are atomic and need enough available money in the source.
