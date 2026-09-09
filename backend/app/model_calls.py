"""Metadata-only accounting at the application model-call seam.

Each call is inserted before dispatch and usage snapshots are committed when received.
A killed process leaves a running/unknown row, never an invented zero-cost success.
Sessions are short and independent: children never share the parent's SQLAlchemy session.
"""
from __future__ import annotations

import math
import uuid
from contextlib import closing
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, replace

from app.config import get_observability_config
from app.database import SessionLocal
from app.models import UsageLedger
from app.usage_ledger import _identity_snapshot

_retry = ContextVar("model_call_retry", default=None)


class IncompleteStreamError(RuntimeError):
    """Transport ended without a confirmed provider completion."""


@dataclass(frozen=True)
class CallScope:
    turn_id: str
    conversation_id: str | None
    user_id: str | None
    kind: str = "agent"
    parent_call_id: str | None = None

    @classmethod
    def new(cls, conversation_id, user_id):
        return cls(uuid.uuid4().hex, conversation_id, user_id)

    def child(self, parent_call_id):
        return replace(self, kind="child", parent_call_id=parent_call_id)


def note_retry():
    """Provider seam for an explicit application retry; SDK-internal retries are opaque."""
    callback = _retry.get()
    if callback:
        callback()


def snapshot_rate(model):
    rate = deepcopy(get_observability_config().get("pricing", {}).get(model))
    if not isinstance(rate, dict):
        return None
    for key in ("input", "output"):
        value = rate.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return None
    return rate


def call_cost(rate, usage):
    if rate is None or usage is None:
        return None
    # Input includes cached tokens; cached rates replace the standard input rate.
    cached = sum(usage.get(k, 0) for k in ("cache_read", "cache_write"))
    value = (usage["input"] - cached) * rate["input"] + usage["output"] * rate["output"]
    for key in ("cache_read", "cache_write"):
        if usage.get(key, 0):
            price = rate.get(key)
            if not isinstance(price, (int, float)) or not math.isfinite(price) or price < 0:
                return None
            value += usage[key] * price
    return round(value / 1_000_000, 8)


def normalize_usage(raw):
    """Usage deltas are cumulative snapshots. Missing counters are explicitly partial."""
    if not isinstance(raw, dict):
        return None
    values = {}
    try:
        for key in ("input", "output", "total", "cache_read", "cache_write", "reasoning"):
            value = raw.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or int(value) != float(value) or int(value) < 0:
                return None
            values[key] = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not values:
        return None
    complete = "input" in values and "output" in values
    inp, out = values.get("input", 0), values.get("output", 0)
    total = values.get("total") or inp + out
    if total < inp + out or values.get("cache_read", 0) + values.get("cache_write", 0) > inp:
        return None
    result = {"input": inp, "output": out, "total": total, "_complete": complete,
              "cache_read": values.get("cache_read", 0), "cache_write": values.get("cache_write", 0)}
    if 'reasoning' in values and 'output' in values and values['reasoning'] <= out:
        result['reasoning'] = values['reasoning']  # A subset of output, never an extra charge.
    return result


class _Call:
    def __init__(self, scope, provider, call_id=None, diagnostics=None):
        self.id = call_id or f"call:{uuid.uuid4().hex}"
        self.rate = snapshot_rate(provider.model)
        self.usage = None
        self.invalid_report = False
        self.diagnostics = deepcopy(diagnostics or {})
        with SessionLocal() as db:
            row = UsageLedger(
                message_id=self.id, turn_id=scope.turn_id, conversation_id=scope.conversation_id,
                parent_call_id=scope.parent_call_id, user_id=scope.user_id,
                profile=getattr(provider, "profile_name", None), model=provider.model,
                call_kind=scope.kind, status="running", usage_status="unknown",
                rate_snapshot=self.rate,
                usage_details={"call": self.diagnostics},
                **{k: v for k, v in _identity_snapshot(db, scope.user_id).items() if k != "user_id"},
            )
            db.add(row)
            db.commit()  # Fail before dispatch if the ledger is unavailable.

    def save(self, status="running"):
        with SessionLocal() as db:
            row = db.query(UsageLedger).filter_by(message_id=self.id).one()
            row.status = status
            row.usage_status = (
                "reported" if status == "completed" and self.usage.get("_complete")
                and not self.invalid_report else "partial"
            ) if self.usage else "unknown"
            if self.usage is not None:
                row.usage_details = {**{k: v for k, v in self.usage.items() if not k.startswith("_")},
                                     "call": deepcopy(self.diagnostics)}
                row.input_tokens = self.usage["input"]
                row.output_tokens = self.usage["output"]
                row.total_tokens = self.usage["total"]
                row.cost_usd = call_cost(self.rate, self.usage)
            else:
                row.usage_details = {"call": deepcopy(self.diagnostics)}
            db.commit()


def stream_model(provider, messages, tools, params, scope, *, cancel_event=None, call_id=None):
    from app.agent.context import fit_context

    params = dict(params)
    window = getattr(provider, "context_window", None)
    if window is not None:
        params["max_context_tokens"] = min(int(window), int(params.get("max_context_tokens", 16000)))
    fitted, info = fit_context(messages, tools, params)
    if info["trimmed"]:
        from app.providers.base import StreamDelta

        yield StreamDelta(type="status", text="Shortening tool output to fit the context budget…")
    if cancel_event is not None and cancel_event.is_set():
        return
    from app.budgets import enforce_budget
    from app.config import get_auth_config
    from app.models import User

    def check_budget():
        with SessionLocal() as db:
            user = db.get(User, scope.user_id) if scope.user_id else None
            if ((user is None and get_auth_config().get("enabled"))
                    or (user is not None and not user.is_active)):
                raise PermissionError("Account is no longer active")
            enforce_budget(db, user or User(id=scope.user_id), provider.model)

    check_budget()
    from app.projects import record_call
    call_id = call_id or f'call:{uuid.uuid4().hex}'
    record_call(scope, provider, fitted, call_id=call_id)
    diagnostics = {
        **info, "max_context_tokens": int(params.get("max_context_tokens", 16000)),
        "max_tool_rounds": params.get("max_tool_rounds"),
        "profile_context_window": window,
        "stage": params.get("_stage") if params.get("_stage") in {
            'plan', 'gather', 'synthesize', 'completion_recovery', 'finalize',
        } else 'generation',
        "finish_reason": None,
        "setting_sources": {k: v for k, v in params.get("_setting_sources", {}).items()
                            if k in {"temperature", "max_tokens", "max_context_tokens", "max_tool_rounds"}
                            and v in {"runtime", "assistant", "conversation_override", "approval_snapshot", "current_limit"}},
    }
    current = _Call(scope, provider, call_id, diagnostics)
    provider_params = {k: v for k, v in params.items() if k not in {'_stage', '_setting_sources'}}
    status = "interrupted"
    terminal = False

    def retry():
        nonlocal current, terminal
        current.save("failed")
        check_budget()
        current = _Call(replace(scope, kind="retry", parent_call_id=current.id), provider,
                        diagnostics=diagnostics)
        record_call(replace(scope, kind='retry'), provider, fitted, call_id=current.id)
        terminal = False

    try:
        with closing(provider.stream(fitted, tools, provider_params)) as source:
            while True:
                token = _retry.set(retry)
                try:
                    delta = next(source)
                except StopIteration:
                    if not terminal:
                        current.diagnostics['finish_reason'] = 'incomplete_stream'
                        raise IncompleteStreamError('Model stream ended without a completion signal. Saved output may be incomplete.') from None
                    status = "completed"
                    break
                finally:
                    _retry.reset(token)
                if delta.type == "usage":
                    normalized = normalize_usage(delta.usage)
                    if normalized is not None:
                        # Repeated/cumulative snapshots must never double-charge.
                        if current.usage and any(normalized[k] < current.usage[k]
                                                 for k in ("input", "output", "total")):
                            current.invalid_report = True
                        else:
                            current.usage = normalized
                        current.save()
                    else:
                        current.invalid_report = True
                if cancel_event is not None and cancel_event.is_set():
                    status = "cancelled"
                    return
                if delta.type in {"done", "tool_calls"}:
                    terminal = True
                    reason = delta.stop_reason or ('tool_calls' if delta.type == 'tool_calls' else 'stop')
                    current.diagnostics['finish_reason'] = reason if reason in {
                        'stop', 'end_turn', 'stop_sequence', 'tool_calls', 'tool_use', 'length',
                        'max_tokens', 'content_filter', 'guardrail_intervened', 'incomplete_stream',
                    } else 'other'
                    if reason == 'incomplete_stream':
                        raise IncompleteStreamError('Model stream ended without a completion signal. Saved output may be incomplete.')
                delta.call_id = current.id
                if cancel_event is not None and cancel_event.is_set():
                    status = "cancelled"
                    return
                yield delta
    except GeneratorExit:
        status = "cancelled" if cancel_event is not None and cancel_event.is_set() else "interrupted"
        raise
    except IncompleteStreamError:
        status = "interrupted"
        raise
    except Exception:
        status = "failed"
        raise
    finally:
        current.save(status)


def summarize_rows(rows):
    usage = {"input": 0, "output": 0, "total": 0, "known_cost": 0.0,
             "unknown_usage_calls": 0, "unknown_cost_calls": 0, "calls": 0}
    for row in rows:
        usage["calls"] += 1
        for key, attr in (("input", "input_tokens"), ("output", "output_tokens"), ("total", "total_tokens")):
            usage[key] += getattr(row, attr) or 0
        usage["known_cost"] += row.cost_usd or 0
        if row.usage_status in {"unknown", "partial"}:
            usage["unknown_usage_calls"] += 1
        if row.cost_usd is None or row.usage_status in {"unknown", "partial"}:
            usage["unknown_cost_calls"] += 1
    usage["known_cost"] = round(usage["known_cost"], 8)
    usage["cost"] = None if usage["unknown_cost_calls"] else usage["known_cost"]
    return usage


def turn_usage(scope):
    with SessionLocal() as db:
        usage = summarize_rows(db.query(UsageLedger).filter_by(turn_id=scope.turn_id).all())
    return {**usage, "accounting": "model_calls", "turn_id": scope.turn_id}


class ScopedProvider:
    """Use the common seam for compaction and gateway paths without changing providers."""
    def __init__(self, provider, scope, *, cancel_event=None, call_id=None):
        self.provider, self.scope = provider, scope
        self.model = provider.model
        self.cancel_event, self.call_id = cancel_event, call_id

    def stream(self, messages, tools, params):
        return stream_model(self.provider, messages, tools, params, self.scope,
                            cancel_event=self.cancel_event, call_id=self.call_id)
