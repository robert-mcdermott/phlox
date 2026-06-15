"""Authentication & authorization.

Pluggable auth: local username/password now, Microsoft Entra ID (OIDC) ready for
production. Sessions are Phlox-issued JWTs regardless of provider, so the rest of the
app is auth-method-agnostic. See ``docs/AUTH.md``.
"""
