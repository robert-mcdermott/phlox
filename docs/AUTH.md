# Authentication & Multi-User

Phlox supports **local username/password** accounts and a hardened **Microsoft Entra ID
(Azure AD) OIDC** authorization-code browser flow. Sessions are Phlox-issued JWTs
regardless of provider, so the rest of the app is auth-method-agnostic.

## How it works

- **Models:** `User` (`models.py`) with `role` (`user` | `admin`), `auth_provider`
  (`local` | `entra`), `password_hash` (bcrypt) for local users, and
  `must_change_password` for restricted bootstrap/reset sessions.
- **Security:** `auth/security.py` — bcrypt hashing + PyJWT HS256 session tokens.
- **Dependencies:** `auth/deps.py` — `get_authenticated_user` validates the session for
  `/me` and password setup; `get_current_user` additionally rejects accounts that still
  have a temporary password; `require_admin` adds the role gate. When `auth.enabled` is
  **false**, a synthetic local admin is returned so the app runs single-user with no login.
- **Endpoints:** `routers/auth.py` — `login`, opt-in/rate-limited `register`, `me`,
  `change-password`, admin `users` CRUD, and the Entra `entra/login` + `entra/callback` +
  one-time `entra/complete` browser flow.
- **Frontend:** `api/token.js` stores the JWT and injects it into every request (REST +
  SSE + uploads); `components/auth/LoginScreen.jsx` gates the app; the Header shows the
  signed-in user + sign-out.

## Roles & isolation

- **Private user data (no admin bypass):** conversations/messages, workspace files,
  attachments, checkpoints, documents, memories, private skills, API keys, and runtime
  settings are scoped strictly by `user_id`. A user only sees their own —
  **including admins**. There is no endpoint that returns another user's chats, and
  ownership checks return **404** (not 403) so existence isn't leaked.
- **Assistant knowledge bases are deliberately shared.** Documents an admin attaches to a
  **custom assistant** are *deployment-owned* (`user_id=NULL`, `assistant_id` set), not
  personal data: every user chatting with that assistant can search them, they never appear
  in anyone's personal Documents panel or `@` picker, they are exempt from
  `delete_user_data` (so deleting the creating admin doesn't destroy a shared knowledge
  base), and retrieval only widens to an assistant's documents after a visibility check on
  the conversation's pinned assistant (see [ARCHITECTURE.md](ARCHITECTURE.md) §3 "Custom
  assistants"). Private (`visibility: private`) assistants are visible only to their
  creator, including when another administrator probes them.
- **Admins manage accounts, not content.** `require_admin` gates MCP server management,
  tool enable/permissions, **custom assistants** (create/edit/delete personas and their
  shared knowledge bases), **deployment configuration** (see below), and **user
  management** — create users, **reset passwords**, set a **department** (for chargeback),
  enable/disable, and **delete accounts**. Deleting an account **purges all of that user's
  data** (chats + workspaces, documents + files + vectors, memories, settings, **API
  keys**, and any **user-scoped spend budget**) via `auth/service.delete_user_data` — the
  admin never reads the content, it is just removed. (A deleted user's gateway keys are
  hard-deleted so they can never authenticate again, while their incurred usage survives in
  the ledger; see [API_GATEWAY.md](API_GATEWAY.md). Department budgets are not tied to one
  account and are left intact — see [BUDGETS.md](BUDGETS.md).)
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
  session_hours: 12
  allow_registration: false     # explicitly opt in to self-service registration
  default_admin:                # username seeded on first run if no users exist
    username: admin
  # Production SSO (Microsoft Entra ID). When tenant_id + client_id are set, the
  # "Sign in with Microsoft" button appears and the OIDC code flow is enabled.
  entra:
    tenant_id: "<tenant-guid>"
    client_id: "<app-client-id>"
    client_secret: "<secret>"
    redirect_uri: "https://your-host/api/auth/entra/callback"
```

On a clean database, Phlox generates a 192-bit random temporary password for this account
and prints it in the colored startup banner. The plaintext is never stored in config or the
database. After login, the session can call only `/api/auth/me` and
`/api/auth/change-password`; every normal UI, admin, data, and gateway path remains blocked
until the password is replaced. Administrator-created accounts and administrator password
resets use the same forced-change behavior.

For production (`PHLOX_ENV=production`), set `PHLOX_JWT_SECRET` to at least 32 bytes of
random material and keep it stable across restarts. Production startup fails closed if it is
missing, short, or a known placeholder. Local development without this variable gets a
strong per-process ephemeral secret, so sessions intentionally do not survive a restart.
To rotate the production secret, schedule a sign-out event, replace it in the deployment
secret store, restart every Phlox process together, and require users to sign in again;
there is currently no overlapping old/new verification window.

Self-service registration is disabled by default. If explicitly enabled, every registered
account receives the `user` role (never first-user/admin promotion) and registration is
process-limited by source IP. Multi-process deployments should add a shared limit at the
reverse proxy as well.

### Entra ID setup (production)
1. Register a single-tenant app in Entra ID; add a Web redirect URI of
   `…/api/auth/entra/callback`. Configure the directory tenant UUID (not `common`,
   `organizations`, or `consumers`).
2. Grant delegated `openid profile email` scopes; create a client secret.
3. Fill `auth.entra` above. New SSO users are created with the `user` role — promote to
   admin in **Settings → Users**.
4. Test the round-trip. Phlox stores a short-lived, one-use random `state`, binds an OIDC
   nonce and PKCE S256 verifier, validates the ID-token signature/issuer/tenant/audience and
   required claims, then redirects with a one-use handoff code. The browser exchanges that
   code for a Phlox JWT; the JWT itself is never placed in a URL. Replays and identity
   collisions with local/other SSO accounts are rejected. The local administrator account
   remains available as a break-glass login.

## Notes / next
- Treat first-run console output as sensitive because it contains the one-time admin
  password. Delete or restrict captured startup logs after the password is changed.
- Audit logging + secrets management are **Tier 5** (sensitive-data deployment). Postgres
  is available now (`DATABASE_URL`, see [DOCKER.md](DOCKER.md)) if you want it sooner.
