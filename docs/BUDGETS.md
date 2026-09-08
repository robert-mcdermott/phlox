# Spend Budgets

[User Guide](USER_GUIDE.md) · [Project overview](../README.md)

Monthly USD spend caps for **users** and **departments**, enforced against the durable
usage ledger. When a budget's current-month spend crosses its warning threshold (default
90%) the user sees a banner; when it reaches or exceeds the limit, **priced** models are
blocked until the calendar month rolls over. Models with no configured price are never
blocked.

## Concepts

- **Budget** — a row scoped to either a single `user` (by user id) or a `department` (by
  name), with a `limit_usd` and a `warn_pct`. Created/managed by admins in
  **Settings → Admin → Budgets**.
- **Priced model** — a model that has a per-million-token entry in
  `observability.pricing` (the same table that drives cost accounting). Only priced models
  count toward spend and only priced models are blocked. Unconfigured models stay usable; an explicit zero-price entry is still a configured model.
- **Most-restrictive-wins** — a user may be covered by both their own user budget and their
  department budget. A turn is blocked if **either** is at/over its limit; the warning fires
  for whichever applicable budget is closest to its limit.
- **Monthly reset** — spend is summed over the current UTC calendar month
  (`[first-of-month, first-of-next-month)`). No stored counter to reset; it simply rolls
  forward because the ledger query is date-bounded.

## Enforcement points

Usage snapshots reach the durable ledger during each model call, including paused work,
children, compaction, fallback, and gateway traffic. Enforcement gates new turns, approval
resumes, and each subsequent application generation call:

- Interactive chat — `POST /api/chat` returns **HTTP 402** at the initial budget gate.
- Approval resume — `POST /api/chat/approve` checks current spend before tools run. A
  rejection leaves the approval pending. Version-3 snapshots are already charged; version-2
  snapshots retain a legacy additional-spend check before import.
- Gateway — `POST /v1/chat/completions` returns an OpenAI-shaped 402 error at preflight;
  `GET /v1/models` annotates configured models with `phlox_blocked: true` over budget.
- Later calls — the shared model-call seam rechecks the ledger before dispatch. Failures
  after an HTTP stream has opened are reported through that stream, not a new HTTP status.

This is not pre-authorization: there are no reservations and an in-flight stream is not
cut off. Parallel calls can overshoot. Unknown provider usage/prices are not treated as
proven zero cost; the budget sum includes only known cost. Models absent from the pricing
map remain allowed by the existing policy. See [MODEL_CALLS.md](MODEL_CALLS.md).

## Data model

`budgets` table (`app/models.py::Budget`), FK-free like the rest of the per-user model:

| column        | meaning                                             |
|---------------|-----------------------------------------------------|
| `scope_type`  | `user` \| `department`                              |
| `scope_value` | the user id, or the department name                 |
| `limit_usd`   | monthly cap in USD                                  |
| `warn_pct`    | warn threshold percent (default 90)                 |
| `is_active`   | soft on/off without deleting                        |

Unique on `(scope_type, scope_value)`.

Deleting a user removes their **user-scoped** budget (it would otherwise linger as an
orphaned config row); **department** budgets are left intact, and the departed user's past
spend still counts toward the department for chargeback via the identity-snapshotted ledger.

## API

- `GET  /api/usage/budget` — the signed-in user's budget status (for the UI banner).
- `GET  /api/admin/budgets` — admin: list budgets with current-month spend each.
- `POST /api/admin/budgets` — admin: create.
- `PATCH /api/admin/budgets/{id}` — admin: update limit / warn / active / scope.
- `DELETE /api/admin/budgets/{id}` — admin: delete.

Core logic lives in `app/budgets.py` (`month_bounds`, `current_month_spend`,
`applicable_budgets`, `budget_status`, `enforce_budget`).
