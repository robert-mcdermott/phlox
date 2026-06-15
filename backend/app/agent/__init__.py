"""PI-Coder-inspired agentic harness.

A minimal, extensible loop: a ``ToolRegistry`` of tools (built-in + MCP + RAG), a
``PermissionGate``, and ``AgentSession.run`` which drives provider tool-call rounds and
emits a normalized ``AgentEvent`` stream consumed by the SSE chat endpoint.
"""
