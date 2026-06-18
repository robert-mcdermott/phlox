# Authentication & Multi-User

Phlox supports **local username/password** accounts now, with a **Microsoft Entra ID
(Azure AD) OIDC** seam ready for production SSO. Sessions are Phlox-issued JWTs
regardless of provider, so the rest of the app is auth-method-agnostic.

## How it works

- **Models:** `User` (`models.py`) with `role` (`user` | `admin`), `auth_provider`
  (`local` | `entra`), and `password_hash` (bcrypt) for local users.
- **Security:** `auth/security.py` — bcrypt hashing + PyJWT HS256 session tokens.
- **Dependencies:** `auth/deps.py` — `get_current_user` (validates the `Authorization:
  Bearer` JWT) and `require_admin`. When `auth.enabled` is **false**, a synthetic local
  admin is returned so the app runs single-user with no login (handy for dev).
- **Endpoints:** `routers/auth.py` — `login`, `register`, `me`, admin `users` CRUD, and
  the Entra `entra/login` + `entra/callback` flow.
- **Frontend:** `api/token.js` stores the JWT and injects it into every request (REST +
  SSE + uploads); `components/auth/LoginScreen.jsx` gates the app; the Header shows the
  signed-in user + sign-out.

## Roles & isolation

- **Private user data (no admin bypass):** conversations, documents, memories, and
  runtime settings are scoped strictly by `user_id`. A user only sees their own —
  **including admins**. There is no endpoint that returns another user's chats, and
  ownership checks return **404** (not 403) so existence isn't leaked.
- **Admins manage accounts, not content.** `require_admin` gates MCP server management,
  tool enable/permissions, **deployment configuration** (see below), and **user
  management** — create users, **reset passwords**, set a **department** (for chargeback),
  enable/disable, and **delete accounts**. Deleting an account **purges all of that user's
  data** (chats + workspaces, documents + files + vectors, memories, settings, **API
  keys**) via `auth/service.delete_user_data` — the admin never reads the content, it is
  just removed. (A deleted user's gateway keys are hard-deleted so they can never
  authenticate again, while their incurred usage survives in the ledger; see
  [API_GATEWAY.md](API_GATEWAY.md).)
- **Admin deployment configuration** (`routers/admin_config.py`, **Settings → (Admin)
  Configuration**): admins edit a curated set of `config.yml` sections **live, without a
  restart** — provider profiles, model pricing, resilience, generation defaults, and sandbox
  limits — stored as a DB overlay (`AppConfig`; see [ARCHITECTURE.md](ARCHITECTURE.md) §6).
  Two security properties matter here: provider **secrets are write-only** — the read API
  masks every credential field (returning only a `*_set` flag) and a save with a blank field
  preserves the existing value, so secrets are never echoed to the browser. This covers all
  of a profile's secrets, including the full set of AWS Bedrock credentials (the single
  Bedrock API key / bearer token, or IAM access key id + secret access key + session token),
  each masked and preserved independently. And the sandbox **runner type**
  (`local` vs `container`) is **not** UI-editable (only its limits are), so isolation can't be
  downgraded at runtime. Bootstrap/security-sensitive settings — `auth.*` (incl. `jwt_secret`
  and `enabled`), `vector_store`, and OTel/request-logging — stay **file-only**.
- **One deliberate exception — the usage ledger.** For departmental **chargeback**, an
  append-only `UsageLedger` records per-turn token usage + cost with the user's identity
  (username/email/department) **snapshotted at write time**, and is **not** purged on
  deletion. This lets a departing user's department still be billed for the month. The
  ledger holds usage **metadata only — never message content** — so it does not expose
  private chats. It is the single intentional carve-out from the "deletion purges
  everything" rule above. See [OBSERVABILITY.md](OBSERVABILITY.md).
- **Pre-auth data:** on startup the first admin claims any `user_id IS NULL` rows so
  legacy/dev data is owned (and private) rather than shared (`auth/service.claim_orphans`).

## Configure (config.yml)

```yaml
auth:
  enabled: true                 # false => single-user dev mode, no login
  jwt_secret: "<32+ byte secret>"   # or set env PHLOX_JWT_SECRET
  session_hours: 12
  allow_registration: true      # disable in production if using SSO only
  default_admin:                # seeded on first run if no users exist
    username: admin
    password: admin             # CHANGE THIS
  # Production SSO (Microsoft Entra ID). When tenant_id + client_id are set, the
  # "Sign in with Microsoft" button appears and the OIDC code flow is enabled.
  entra:
    tenant_id: "<tenant-guid>"
    client_id: "<app-client-id>"
    client_secret: "<secret>"
    redirect_uri: "https://your-host/api/auth/entra/callback"
```

### Entra ID setup (production)
1. Register an app in Entra ID; add a Web redirect URI of `…/api/auth/entra/callback`.
2. Grant delegated `openid profile email` scopes; create a client secret.
3. Fill `auth.entra` above. New SSO users are created with the `user` role — promote to
   admin in **Settings → Users**.
4. Test the round-trip (`entra/login` → Microsoft → `entra/callback` issues a Phlox
   JWT). The local/admin account still works as a break-glass login.

## Notes / next
- The default dev secret and `admin/admin` are **for dev only** — set a real secret and
  change the admin password before any shared use.
- Postgres + audit logging + secrets management are **Tier 5** (sensitive-data deployment).
