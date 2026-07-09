"""Guardrails: rule compilation, redaction/blocking, the streaming redactor's
chunk-straddle safety, the admin config/preview API, and enforcement at both model-call
choke points (the /v1 gateway and interactive chat)."""
import json

import pytest

from app import app_config
from app.guardrails import StreamRedactor, apply_rules, get_rules, scrub_messages
from app.providers.base import StreamDelta


def _sse_events(body: str) -> list[dict]:
    """Parse every ``data: {json}`` SSE frame in a response body."""
    out = []
    for line in body.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                out.append(json.loads(line[6:]))
            except ValueError:
                pass
    return out


SAMPLE ="Contact me at jane@example.com, call 206-555-2407, my SSN is 123-45-6789."


def _policy(**over):
    base = {
        "enabled": True,
        "input_action": "redact",
        "output_action": "off",
        "redaction_text": "[REDACTED]",
        "builtin": {},
        "custom_patterns": [],
    }
    base.update(over)
    return base


@pytest.fixture
def clear_overlay():
    yield
    app_config.set_section("guardrails", None)
    app_config.invalidate()


# --- core rules ---------------------------------------------------------------
def test_builtin_redaction():
    rules = get_rules("input", _policy())
    res = apply_rules(SAMPLE, rules)
    assert "[EMAIL]" in res.text and "[PHONE]" in res.text and "[SSN]" in res.text
    assert "jane@example.com" not in res.text
    assert "206-555-2407" not in res.text
    assert "123-45-6789" not in res.text
    assert not res.blocked


def test_api_key_and_credit_card_detectors():
    rules = get_rules("input", _policy())
    text = "key sk-abcdefghijklmnop1234 card 4111 1111 1111 1111 aws AKIAIOSFODNN7EXAMPLE"
    res = apply_rules(text, rules)
    assert res.text.count("[API_KEY]") == 2
    assert "[CREDIT_CARD]" in res.text


def test_disabled_policy_and_off_direction_produce_no_rules():
    assert get_rules("input", _policy(enabled=False)) == []
    assert get_rules("output", _policy(output_action="off")) == []
    assert get_rules("input", _policy(input_action="off")) == []


def test_builtin_toggle_off():
    rules = get_rules("input", _policy(builtin={"email": False}))
    res = apply_rules("mail jane@example.com ssn 123-45-6789", rules)
    assert "jane@example.com" in res.text  # email detector disabled
    assert "[SSN]" in res.text


def test_custom_pattern_redact_and_block():
    pol = _policy(custom_patterns=[
        {"name": "MRN", "regex": r"\bMRN-\d{6}\b", "action": "redact", "replacement": "[MRN]"},
        {"name": "codename", "regex": r"\bproject-nightjar\b", "action": "block"},
    ])
    rules = get_rules("input", pol)
    res = apply_rules("patient MRN-123456", rules)
    assert res.text == "patient [MRN]" and not res.blocked

    res = apply_rules("about project-nightjar", rules)
    assert res.blocked
    assert "project-nightjar" not in res.text  # block also redacts
    assert "codename" in res.matched


def test_invalid_custom_regex_is_skipped():
    pol = _policy(custom_patterns=[{"name": "bad", "regex": "([unclosed", "action": "redact"}])
    rules = get_rules("input", pol)  # must not raise
    assert all(r.name != "bad" for r in rules)


def test_block_direction_action_blocks_builtins():
    rules = get_rules("input", _policy(input_action="block"))
    res = apply_rules("reach me: jane@example.com", rules)
    assert res.blocked and "[EMAIL]" in res.text


def test_scrub_messages_copies_and_skips_non_text():
    rules = get_rules("input", _policy())
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "mail jane@example.com", "images": ["data:..."]},
        {"role": "tool", "tool_call_id": "1", "content": "ssn 123-45-6789"},
    ]
    out, matched, blocked = scrub_messages(msgs, rules)
    assert out[1]["content"] == "mail [EMAIL]"
    assert out[2]["content"] == "ssn [SSN]"
    assert msgs[1]["content"] == "mail jane@example.com"  # original untouched
    assert out[1]["images"] == ["data:..."]
    assert {"Email address", "Social Security number"} <= matched and not blocked


# --- streaming redactor ---------------------------------------------------------
def test_stream_redactor_redacts_match_split_across_chunks():
    rules = get_rules("output", _policy(output_action="redact"))
    r = StreamRedactor(rules, holdback=32)
    text = "Sure — write to jane.doe@example.com for details, and that's everything."
    out = ""
    for i in range(0, len(text), 3):  # 3-char chunks split the email mid-match
        out += r.feed(text[i:i + 3])
    out += r.flush()
    assert out == "Sure — write to [EMAIL] for details, and that's everything."


def test_stream_redactor_holds_back_partial_match_at_buffer_end():
    rules = get_rules("output", _policy(output_action="redact"))
    r = StreamRedactor(rules, holdback=16)
    emitted = r.feed("x" * 40 + " jane@exa")  # potential email prefix in the tail
    assert "jane@exa" not in emitted
    emitted += r.feed("mple.com and more trailing text to push the emit window")
    emitted += r.flush()
    assert "jane@example.com" not in emitted
    assert "[EMAIL]" in emitted


def test_stream_redactor_block_detected_while_held_back():
    pol = _policy(output_action="redact",
                  custom_patterns=[{"name": "codename", "regex": r"nightjar", "action": "block"}])
    r = StreamRedactor(get_rules("output", pol))
    r.feed("the secret is night")
    assert not r.blocked
    r.feed("jar, don't tell")
    assert r.blocked and "codename" in r.matched


def test_stream_redactor_passthrough_without_rules():
    r = StreamRedactor([])
    assert r.feed("anything at all") == "anything at all"
    assert r.flush() == ""


# --- admin API -------------------------------------------------------------------
def test_admin_config_roundtrip_and_validation(client, clear_overlay):
    cfg = client.get("/api/admin/config").json()
    assert cfg["guardrails"]["enabled"] is False  # default off
    assert any(b["id"] == "email" and b["regex"] for b in cfg["guardrails_builtins"])

    r = client.put("/api/admin/config/guardrails", json=_policy(
        custom_patterns=[{"name": "MRN", "regex": r"\bMRN-\d{6}\b",
                          "action": "redact", "replacement": "[MRN]"}],
    ))
    assert r.status_code == 200
    assert r.json()["guardrails"]["enabled"] is True

    # Invalid regex / action / builtin id are rejected with a pointed 422.
    bad = _policy(custom_patterns=[{"name": "bad", "regex": "([", "action": "redact"}])
    assert client.put("/api/admin/config/guardrails", json=bad).status_code == 422
    assert client.put("/api/admin/config/guardrails",
                      json=_policy(input_action="nope")).status_code == 422
    assert client.put("/api/admin/config/guardrails",
                      json=_policy(builtin={"nope": True})).status_code == 422


def test_preview_endpoint_uses_inline_policy(client):
    r = client.post("/api/admin/config/guardrails/preview",
                    json={"text": SAMPLE, "direction": "input", "policy": _policy()})
    body = r.json()
    assert r.status_code == 200
    assert "[EMAIL]" in body["result"] and not body["blocked"] and body["active"]

    # Without an inline policy it previews the active config (default: disabled).
    r = client.post("/api/admin/config/guardrails/preview",
                    json={"text": SAMPLE, "direction": "input"})
    assert r.json()["active"] is False
    assert r.json()["result"] == SAMPLE


# --- gateway enforcement -----------------------------------------------------------
class PIIProvider:
    """Streams a completion containing an email, split across chunk boundaries."""

    model = "test-model"
    supports_tools = True

    def __init__(self):
        self.seen_messages = None

    def stream(self, messages, tools, params):  # noqa: ARG002
        self.seen_messages = messages
        yield StreamDelta(type="text", text="Write to jane.d")
        yield StreamDelta(type="text", text="oe@example.com today.")
        yield StreamDelta(type="usage", usage={"input": 5, "output": 5, "total": 10})
        yield StreamDelta(type="done", stop_reason="stop")


def _gateway_setup(client, monkeypatch, policy):
    import app.routers.gateway as gw

    app_config.set_section("guardrails", policy)
    provider = PIIProvider()
    monkeypatch.setattr(gw, "build_provider", lambda profile, model: provider)
    key = client.post("/api/api-keys", json={"name": "grd"}).json()["key"]
    return provider, {"Authorization": f"Bearer {key}"}


def test_gateway_input_redacted_before_provider(client, monkeypatch, clear_overlay):
    provider, headers = _gateway_setup(client, monkeypatch, _policy())
    r = client.post("/v1/chat/completions", headers=headers, json={
        "model": "test-model",
        "messages": [{"role": "user", "content": f"summarize: {SAMPLE}"}],
    })
    assert r.status_code == 200
    sent = provider.seen_messages[0]["content"]
    assert "jane@example.com" not in sent and "[EMAIL]" in sent


def test_gateway_input_block(client, monkeypatch, clear_overlay):
    _, headers = _gateway_setup(client, monkeypatch, _policy(input_action="block"))
    r = client.post("/v1/chat/completions", headers=headers, json={
        "model": "test-model",
        "messages": [{"role": "user", "content": SAMPLE}],
    })
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "guardrails_violation"


def test_gateway_output_redact_buffered_and_streaming(client, monkeypatch, clear_overlay):
    _, headers = _gateway_setup(client, monkeypatch,
                                _policy(input_action="off", output_action="redact"))
    body = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}

    r = client.post("/v1/chat/completions", headers=headers, json=body)
    content = r.json()["choices"][0]["message"]["content"]
    assert content == "Write to [EMAIL] today."

    r = client.post("/v1/chat/completions", headers=headers, json={**body, "stream": True})
    text = "".join(
        ev["choices"][0]["delta"].get("content", "")
        for ev in _sse_events(r.text) if "choices" in ev
    )
    assert text == "Write to [EMAIL] today."


def test_gateway_output_block_rejects_streaming_and_blocks_buffered(
    client, monkeypatch, clear_overlay
):
    _, headers = _gateway_setup(client, monkeypatch,
                                _policy(input_action="off", output_action="block"))
    body = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}

    r = client.post("/v1/chat/completions", headers=headers, json={**body, "stream": True})
    assert r.status_code == 400
    assert "stream" in r.json()["error"]["message"].lower()

    r = client.post("/v1/chat/completions", headers=headers, json=body)
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "guardrails_violation"


def test_gateway_stream_aborts_on_custom_block_match(client, monkeypatch, clear_overlay):
    pol = _policy(input_action="off", output_action="redact", custom_patterns=[
        {"name": "codename", "regex": r"jane\.doe", "action": "block"},
    ])
    _, headers = _gateway_setup(client, monkeypatch, pol)
    r = client.post("/v1/chat/completions", headers=headers, json={
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert "guardrails_violation" in r.text
    assert "jane.doe" not in r.text


# --- interactive chat enforcement ---------------------------------------------------
def test_chat_input_block_and_provider_scrub(client, monkeypatch, clear_overlay):
    import app.routers.chat as chat_mod

    app_config.set_section("guardrails", _policy())
    provider = PIIProvider()
    monkeypatch.setattr(chat_mod, "build_provider", lambda profile, model=None: provider)

    # Redact mode: the turn runs; the provider must only ever see scrubbed content.
    r = client.post("/api/chat", json={"message": f"note {SAMPLE}"})
    assert r.status_code == 200
    joined = " ".join(m.get("content", "") or "" for m in provider.seen_messages)
    assert "jane@example.com" not in joined and "[EMAIL]" in joined
    assert any(ev.get("type") == "done" for ev in _sse_events(r.text))

    # Block mode refuses the turn up front (nothing persisted, like the budget 402).
    app_config.set_section("guardrails", _policy(input_action="block"))
    r = client.post("/api/chat", json={"message": SAMPLE})
    assert r.status_code == 400
    assert "guardrails" in r.json()["detail"].lower()


def test_chat_output_redaction_in_sse(client, monkeypatch, clear_overlay):
    import app.routers.chat as chat_mod

    app_config.set_section("guardrails", _policy(input_action="off", output_action="redact"))
    monkeypatch.setattr(chat_mod, "build_provider", lambda profile, model=None: PIIProvider())

    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    tokens = "".join(
        ev.get("content", "") for ev in _sse_events(r.text) if ev.get("type") == "token"
    )
    assert "jane.doe@example.com" not in tokens
    assert "[EMAIL]" in tokens
