"""Seed a Phlox database with demo users, departments, activity, and budgets.

Populates a (preferably clean) phlox.db with everything needed to demo the usage
reporting (Settings -> Admin -> Usage) and budgeting (Settings -> Admin -> Budgets)
features:

- 20 believable users across 5 departments (Bridgeworks, SoftwareEng, SciComp,
  Bioinformatics, Datascience), all with password ``Demo123!``.
- The default admin account (admin / admin) if no users exist yet.
- The model pricing overlay (same priced models as the source deployment) so cost
  computation and budget enforcement work on a fresh DB. Existing pricing is kept.
- ~2.5 months of conversations + messages with realistic token usage, priced via the
  app's own ``compute_cost``, then backfilled into the durable UsageLedger through the
  app's real backfill path (identical attribution to live traffic).
- A monthly budget per department, tuned so that **Bridgeworks is over its limit**
  (priced models blocked) and **SoftwareEng is in the warning band** (>= warn_pct,
  < 100%). The other departments sit at healthy utilization.

Usage (from the backend/ directory):

    uv run python scripts/seed_demo.py            # seed (aborts if demo users exist)
    uv run python scripts/seed_demo.py --reset    # purge previous demo data, then seed
    uv run python scripts/seed_demo.py --seed 7   # different random variation

To keep your current DB untouched and use a separate data directory the backend honors a PHLOX_DATA environment variable for where phlox.db (plus uploads/workspaces/attachments) lives. Point both the seed script and the server at a demo directory:

```
cd backend
PHLOX_DATA=./data-demo uv run python scripts/seed_demo.py
PHLOX_DATA=./data-demo uv run uvicorn app.main:app --reload --port 8000
```
This is how I tested the script, and it's the nicer setup for demos: your day-to-day

The script talks straight to the database via the app's own modules — the backend does
not need to be running (stop it first if it is, to avoid SQLite lock contention).
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the ``app`` package importable when run as ``python scripts/seed_demo.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app_config  # noqa: E402
from app.auth.service import create_user, get_by_username, seed_default_admin  # noqa: E402
from app.budgets import budget_status, current_month_spend, month_bounds  # noqa: E402
from app.config import get_observability_config, get_profiles  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Budget, Conversation, Message, UsageLedger, User  # noqa: E402
from app.observability import compute_cost  # noqa: E402
from app.usage_ledger import backfill_usage_ledger  # noqa: E402

DEMO_PASSWORD = "Demo123!"

# Pricing seeded into the admin config overlay when none exists yet (USD per 1M tokens).
# Snapshot of the source deployment's priced models — keeps budget enforcement and cost
# reporting working on a brand-new phlox.db without touching provider profiles/secrets.
DEMO_PRICING = {
    "us.anthropic.claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "us.anthropic.claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "minimax.minimax-m2.5": {"input": 0.3, "output": 1.2},
    "moonshotai.kimi-k2.5": {"input": 0.6, "output": 3.0},
    "gemma4:31b-cloud": {"input": 0.1, "output": 1.0},
}

# Department -> (monthly limit USD, current-month spend as a fraction of the limit).
# Bridgeworks lands over 100% (blocked); SoftwareEng lands in the 90-100% warn band.
DEPARTMENTS = {
    "Bridgeworks": {"limit": 50.0, "target_pct": 1.06},
    "SoftwareEng": {"limit": 60.0, "target_pct": 0.94},
    "SciComp": {"limit": 75.0, "target_pct": 0.52},
    "Bioinformatics": {"limit": 40.0, "target_pct": 0.33},
    "Datascience": {"limit": 100.0, "target_pct": 0.67},
}
WARN_PCT = 90

DEMO_USERS = [
    # (display name, department)
    ("Sarah Okafor", "Bridgeworks"),
    ("Miguel Torres", "Bridgeworks"),
    ("Priya Raman", "Bridgeworks"),
    ("Daniel Kowalski", "Bridgeworks"),
    ("Emily Nakamura", "SoftwareEng"),
    ("James Whitfield", "SoftwareEng"),
    ("Ana Petrova", "SoftwareEng"),
    ("Marcus Lee", "SoftwareEng"),
    ("Rachel Lindqvist", "SciComp"),
    ("Tom Beaumont", "SciComp"),
    ("Fatima El-Sayed", "SciComp"),
    ("Kevin Brennan", "SciComp"),
    ("Laura Chen", "Bioinformatics"),
    ("David Osei", "Bioinformatics"),
    ("Ingrid Halvorsen", "Bioinformatics"),
    ("Sameer Patel", "Bioinformatics"),
    ("Nina Rossi", "Datascience"),
    ("Chris Duggan", "Datascience"),
    ("Mei-Ling Wu", "Datascience"),
    ("Alex Vargas", "Datascience"),
]

CONVERSATION_TITLES = {
    "Bridgeworks": [
        "Load model sanity check", "Girder spec summary", "Client RFP draft",
        "Inspection report cleanup", "Bridge deck material comparison",
        "Cost estimate walkthrough", "CAD export script help",
    ],
    "SoftwareEng": [
        "Refactor auth middleware", "CI pipeline flakiness", "API pagination design",
        "Debugging websocket drops", "Code review: retry logic",
        "Migration script for orders table", "Perf profile of ingest service",
    ],
    "SciComp": [
        "MPI job hangs on 128 ranks", "Slurm array job template", "FFT precision question",
        "Fortran to Python port", "GPU kernel occupancy", "Mesh refinement strategy",
        "Batch queue policy summary",
    ],
    "Bioinformatics": [
        "RNA-seq QC thresholds", "VCF annotation pipeline", "Nextflow config help",
        "BLAST output parsing", "Single-cell clustering params",
        "Variant calling comparison", "Genome assembly stats",
    ],
    "Datascience": [
        "Churn model feature ideas", "Forecast backtest setup", "SQL window function help",
        "Dashboard metric definitions", "A/B test power analysis",
        "Embedding drift monitoring", "Notebook cleanup for handoff",
    ],
}

USER_PROMPTS = [
    "Can you help me work through this? I've pasted the details below.",
    "Here's what I have so far — what would you change?",
    "Summarize the key points and flag anything that looks wrong.",
    "Write a first draft I can edit, keep it concise.",
    "Walk me through the trade-offs here.",
    "This is failing intermittently — help me narrow down why.",
    "Turn these rough notes into something I can share with the team.",
]

ASSISTANT_REPLIES = [
    "Here's a breakdown of the main points, with the risky areas flagged at the end.",
    "I worked through it step by step — the short answer is yes, with two caveats.",
    "Draft below. I kept the structure simple so it's easy to trim.",
    "The likely culprit is the second case; here's how to confirm and fix it.",
    "I compared the options across cost, effort, and risk — summary table below.",
    "Done. I also noticed a small inconsistency you may want to clean up.",
    "Here's the revised version with the changes you asked for.",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _username(display_name: str) -> str:
    first, last = display_name.lower().split(" ", 1)
    return (first[0] + last.replace(" ", "").replace("-", "")).replace("'", "")


def _email(display_name: str) -> str:
    return display_name.lower().replace(" ", ".").replace("'", "") + "@example.com"


def _draw_tokens(rng: random.Random) -> tuple[int, int]:
    """Plausible per-turn token counts: light chat turns vs heavy agentic turns."""
    if rng.random() < 0.55:  # heavy agentic turn (tool rounds replay lots of context)
        return rng.randint(20_000, 140_000), rng.randint(1_500, 8_000)
    return rng.randint(800, 6_000), rng.randint(150, 1_500)


def _tokens_for_cost(model: str, pricing: dict, cost_usd: float) -> tuple[int, int]:
    """Invert pricing: token counts whose cost is ~``cost_usd`` (70/30 input/output)."""
    rate = pricing[model]
    inp = int(cost_usd * 0.7 / max(rate.get("input", 0.01), 0.01) * 1_000_000)
    out = int(cost_usd * 0.3 / max(rate.get("output", 0.01), 0.01) * 1_000_000)
    return max(inp, 1), max(out, 1)


def _random_time(rng: random.Random, lo: datetime, hi: datetime) -> datetime:
    """A working-hours-biased naive-UTC timestamp in [lo, hi)."""
    span = max(int((hi - lo).total_seconds()), 1)
    t = lo + timedelta(seconds=rng.randint(0, span - 1))
    return t.replace(hour=rng.randint(8, 19), minute=rng.randint(0, 59),
                     second=rng.randint(0, 59), microsecond=0)


def _free_model() -> str:
    """A model that has no pricing entry (usage shows up but never counts to budgets)."""
    priced = set(DEMO_PRICING)
    for profile in get_profiles().values():
        for m in profile.get("models") or []:
            if m not in priced:
                return m
    return "gemma4:12b"


def _month_windows(n: int = 3) -> list[tuple[datetime, datetime]]:
    """[(start, end)] for the current month and the ``n - 1`` months before it,
    oldest first; the current month's window is clamped to *now*."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start, _ = month_bounds()
    windows = [(start, now)]
    for _i in range(n - 1):
        end = start
        start = (start - timedelta(days=1)).replace(day=1)
        windows.append((start, end))
    return list(reversed(windows))


# ---------------------------------------------------------------------------
# Purge / seed
# ---------------------------------------------------------------------------
def purge_demo_data(db) -> None:
    usernames = [_username(n) for n, _ in DEMO_USERS]
    users = db.query(User).filter(User.username.in_(usernames)).all()
    ids = [u.id for u in users]
    if ids:
        convs = db.query(Conversation).filter(Conversation.user_id.in_(ids)).all()
        for c in convs:  # cascade deletes messages
            db.delete(c)
        db.query(UsageLedger).filter(UsageLedger.user_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(Budget).filter(Budget.scope_type == "user",
                                Budget.scope_value.in_(ids)).delete(synchronize_session=False)
        for u in users:
            db.delete(u)
    db.query(Budget).filter(Budget.scope_type == "department",
                            Budget.scope_value.in_(DEPARTMENTS)).delete(synchronize_session=False)
    db.commit()
    print(f"Purged previous demo data ({len(ids)} users).")


def seed_pricing() -> dict:
    """Ensure the pricing overlay exists; return the effective pricing map."""
    if app_config.get_section("pricing") is None:
        app_config.set_section("pricing", DEMO_PRICING)
        print(f"Seeded model pricing overlay ({len(DEMO_PRICING)} priced models).")
    pricing = get_observability_config().get("pricing") or {}
    print("Priced models: " + ", ".join(sorted(pricing)))
    return pricing


def seed_users(db) -> dict[str, list[User]]:
    by_dept: dict[str, list[User]] = {d: [] for d in DEPARTMENTS}
    for name, dept in DEMO_USERS:
        user = create_user(
            db,
            username=_username(name),
            password=DEMO_PASSWORD,
            email=_email(name),
            display_name=name,
            department=dept,
        )
        by_dept[dept].append(user)
    print(f"Created {len(DEMO_USERS)} users across {len(DEPARTMENTS)} departments "
          f"(password: {DEMO_PASSWORD}).")
    return by_dept


def seed_activity(db, rng: random.Random, by_dept: dict[str, list[User]],
                  pricing: dict) -> None:
    """Conversations + messages whose current-month cost per department lands exactly on
    ``limit * target_pct`` (prior months get unconstrained realistic spend)."""
    priced = sorted(pricing)
    free_model = _free_model()
    windows = _month_windows(3)
    current_start = windows[-1][0]
    n_msgs = 0

    for dept, cfg in DEPARTMENTS.items():
        users = by_dept[dept]
        # Per-department model preference (biased toward pricier models so turn counts
        # stay plausible) + per-user activity weights.
        model_weights = [
            (pricing[m].get("input", 0) + pricing[m].get("output", 0)) ** 0.5
            * rng.uniform(0.6, 1.8)
            for m in priced
        ]
        user_weights = [rng.uniform(0.5, 2.5) for _ in users]
        convs: dict[str, list[Conversation]] = {u.id: [] for u in users}

        def add_turn(user: User, model: str, inp: int, out: int,
                     lo: datetime, hi: datetime) -> float:
            nonlocal n_msgs
            t = _random_time(rng, lo, hi)
            pool = convs[user.id]
            if not pool or (len(pool) < 18 and rng.random() < 0.06):
                conv = Conversation(
                    user_id=user.id,
                    title=rng.choice(CONVERSATION_TITLES[user.department]),
                    created_at=t, updated_at=t,
                )
                db.add(conv)
                db.flush()
                pool.append(conv)
            conv = rng.choice(pool)
            conv.updated_at = max(conv.updated_at or t, t)
            cost = compute_cost(model, {"input": inp, "output": out})
            usage = {"input": inp, "output": out, "total": inp + out}
            if cost is not None:
                usage["cost"] = cost
            db.add(Message(id=uuid.uuid4().hex, conversation_id=conv.id, role="user",
                           content=rng.choice(USER_PROMPTS),
                           created_at=t - timedelta(seconds=rng.randint(20, 90))))
            db.add(Message(id=uuid.uuid4().hex, conversation_id=conv.id, role="assistant",
                           content=rng.choice(ASSISTANT_REPLIES), usage=usage,
                           model=model, created_at=t))
            n_msgs += 2
            return cost or 0.0

        for lo, hi in windows:
            is_current = lo == current_start
            if is_current:
                # Land exactly on the target, net of any pre-existing ledger spend.
                target = cfg["limit"] * cfg["target_pct"]
                target -= current_month_spend(db, scope_type="department", scope_value=dept)
            else:
                target = cfg["limit"] * rng.uniform(0.55, 0.9)

            spent, priced_turns = 0.0, 0
            while spent < target:
                user = rng.choices(users, weights=user_weights)[0]
                model = rng.choices(priced, weights=model_weights)[0]
                inp, out = _draw_tokens(rng)
                cost = compute_cost(model, {"input": inp, "output": out}) or 0.0
                final_topup = is_current and spent + cost > target
                if final_topup:
                    # Final top-up turn sized to land right on the target.
                    inp, out = _tokens_for_cost(model, pricing, target - spent)
                spent += add_turn(user, model, inp, out, lo, hi)
                priced_turns += 1
                if final_topup:
                    break

            # Sprinkle free/unpriced-model turns: visible in reports, $0 to budgets.
            for _i in range(int(priced_turns * rng.uniform(0.15, 0.3))):
                user = rng.choices(users, weights=user_weights)[0]
                inp = rng.randint(500, 20_000)
                add_turn(user, free_model, inp, rng.randint(100, 3_000), lo, hi)

        db.commit()
        print(f"  {dept:<15} activity generated "
              f"(current-month priced spend ~${cfg['limit'] * cfg['target_pct']:.2f})")

    created = backfill_usage_ledger(db)
    print(f"Created {n_msgs} messages; backfilled {created} usage-ledger rows.")


def seed_budgets(db) -> None:
    for dept, cfg in DEPARTMENTS.items():
        db.add(Budget(scope_type="department", scope_value=dept,
                      limit_usd=cfg["limit"], warn_pct=WARN_PCT, is_active=True))
    db.commit()
    print(f"Created {len(DEPARTMENTS)} department budgets (warn at {WARN_PCT}%).")


def verify(db) -> None:
    print("\nDepartment budget status (current month):")
    print(f"  {'department':<15} {'spent':>10} {'limit':>10} {'pct':>7}  state")
    problems = []
    for dept, cfg in DEPARTMENTS.items():
        user = db.query(User).filter(User.department == dept).first()
        status = budget_status(db, user)
        cell = next(c for c in status["budgets"] if c["scope_value"] == dept)
        state = "OVER (blocked)" if status["blocked"] else (
            "WARNING" if status["warn"] else "ok")
        print(f"  {dept:<15} ${cell['spent_usd']:>9.2f} ${cell['limit_usd']:>9.2f} "
              f"{cell['pct']:>6.1f}%  {state}")
        if dept == "Bridgeworks" and not status["blocked"]:
            problems.append("Bridgeworks is not over budget")
        if dept == "SoftwareEng" and not status["warn"]:
            problems.append("SoftwareEng is not in the warning band")
    if problems:
        sys.exit("SEED FAILED verification: " + "; ".join(problems))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true",
                        help="purge previously seeded demo data before seeding")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    args = parser.parse_args()
    rng = random.Random(args.seed)

    init_db()
    db = SessionLocal()
    try:
        if get_by_username(db, _username(DEMO_USERS[0][0])):
            if not args.reset:
                sys.exit("Demo users already exist — rerun with --reset to reseed.")
            purge_demo_data(db)

        seed_default_admin(db)  # admin/admin on a fresh DB; no-op otherwise
        pricing = seed_pricing()
        by_dept = seed_users(db)
        print("Generating conversations, messages, and ledger rows...")
        seed_activity(db, rng, by_dept, pricing)
        seed_budgets(db)
        verify(db)
    finally:
        db.close()

    print(f"""
Done. Demo walkthrough:
  - Admin (admin/admin unless changed): Settings -> Admin -> Usage for the chargeback
    report, Settings -> Admin -> Budgets for per-department spend vs limit.
  - Log in as a Bridgeworks user (e.g. sokafor / {DEMO_PASSWORD}): priced models are
    blocked — the budget-exceeded state.
  - Log in as a SoftwareEng user (e.g. enakamura / {DEMO_PASSWORD}): the budget
    warning banner is showing.
  - Other departments (e.g. nrossi / {DEMO_PASSWORD}) are at healthy utilization.
""")


if __name__ == "__main__":
    main()
