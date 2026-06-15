"""Unit tests for pure logic: security, context compaction, workspace, RAG."""
import pytest


# -- auth security ---------------------------------------------------------
def test_password_hash_and_verify():
    from app.auth.security import hash_password, verify_password

    h = hash_password("s3cret")
    assert h != "s3cret"
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", None)


def test_jwt_roundtrip():
    from app.auth.security import create_access_token, decode_access_token

    tok = create_access_token("user-1", "admin")
    payload = decode_access_token(tok)
    assert payload and payload["sub"] == "user-1" and payload["role"] == "admin"
    assert decode_access_token("garbage") is None


# -- workspace traversal guard --------------------------------------------
def test_resolve_in_workspace_blocks_escape():
    from app.workspace.manager import resolve_in_workspace

    inside = resolve_in_workspace("conv-test", "sub/file.txt")
    assert "conv-test" in str(inside)
    for bad in ("../../etc/passwd", "..\\..\\windows", "/etc/passwd"):
        with pytest.raises(ValueError):
            resolve_in_workspace("conv-test", bad)


# -- RAG primitives --------------------------------------------------------
def test_sparse_embed_deterministic_and_overlap():
    from app.rag.embed import sparse_embed

    a = sparse_embed("the quick brown fox")
    b = sparse_embed("the quick brown fox")
    assert a == b  # deterministic
    assert a["indices"] and len(a["indices"]) == len(a["values"])
    # shared terms -> overlapping indices
    c = sparse_embed("the lazy brown dog")
    assert set(a["indices"]) & set(c["indices"])


def test_rrf_fuse_orders_by_combined_rank():
    from app.rag.store import _rrf_fuse

    class P:
        def __init__(self, i):
            self.id = i
            self.payload = {"id": i}

    dense = [P("a"), P("b"), P("c")]
    sparse = [P("b"), P("a"), P("d")]
    fused = _rrf_fuse([dense, sparse], limit=3)
    # 'a' and 'b' appear in both lists near the top -> should rank above c/d.
    top_ids = [f["payload"]["id"] for f in fused[:2]]
    assert set(top_ids) == {"a", "b"}


# -- context compaction ----------------------------------------------------
def test_compact_history_summarizes_old_turns():
    from app.agent.context import compact_history, estimate_tokens
    from app.providers.base import StreamDelta

    class FakeProvider:
        model = "fake"
        supports_tools = True

        def stream(self, messages, tools, params):
            yield StreamDelta(type="text", text="[summary]")
            yield StreamDelta(type="done", stop_reason="stop")

    msgs = [{"role": "system", "content": "sys"}]
    for i in range(12):
        msgs.append({"role": "user", "content": "x" * 4000 + f" turn {i}"})
        msgs.append({"role": "assistant", "content": "y" * 2000})

    out, did = compact_history(FakeProvider(), msgs, max_tokens=5000, keep_last_turns=3)
    assert did
    assert estimate_tokens(out) < estimate_tokens(msgs)
    assert out[0]["role"] == "system"
    assert any("summary" in (m.get("content") or "").lower() for m in out)


def test_compute_cost_from_pricing(monkeypatch):
    from app import observability

    monkeypatch.setattr(
        observability, "get_observability_config",
        lambda: {"pricing": {"m": {"input": 3.0, "output": 15.0}}},
    )
    cost = observability.compute_cost("m", {"input": 1_000_000, "output": 1_000_000})
    assert cost == pytest.approx(18.0)
    assert observability.compute_cost("unknown", {"input": 100}) is None


# -- bedrock auth modes ----------------------------------------------------
def test_bedrock_auth_modes_select_signer():
    """The single Bedrock API key uses bearer auth; IAM creds and the default chain use
    SigV4. Construction must not require network access."""
    from app.providers.bedrock_provider import BedrockProvider

    base = {"model": "us.anthropic.claude-sonnet-4-6", "aws_region": "us-west-2"}

    bearer = BedrockProvider({**base, "aws_bedrock_api_key": "bearer-token"})
    assert bearer._client.meta.config.signature_version == "bearer"

    iam = BedrockProvider({**base, "aws_access_key_id": "AKIA", "aws_secret_access_key": "s"})
    assert iam._client.meta.config.signature_version == "v4"

    chain = BedrockProvider(base)  # default credential chain (no explicit creds)
    assert chain._client.meta.config.signature_version == "v4"
