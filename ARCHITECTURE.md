# The Board Room — Architecture & Security

A short explanation of how the BoardLog web app is put together and why the
security model holds up.

## Components

```
┌──────────────────────────┐         ┌──────────────────────────────┐         ┌─────────────────┐
│  GitHub Pages (static)   │  HTTPS  │   AWS Lambda (Function URL)   │  HTTPS  │  Tension API    │
│  docs/  index.html       │ ──────▶ │   backend/boardlog_lambda     │ ──────▶ │  /sessions,/sync│
│  app.js  site.config.js  │         │   handler.lambda_handler      │         │                 │
│  (PUBLIC, no secrets)    │ ◀────── │                               │ ◀────── │                 │
└──────────────────────────┘  JSON   └───────────────┬──────────────┘  JSON    └─────────────────┘
                                                      │ ssm:GetParameter (KMS decrypt)
                                                      ▼
                                       ┌──────────────────────────────┐
                                       │  SSM Parameter Store          │
                                       │  /boardlog/gate-phrase  (enc) │
                                       │  /boardlog/access-key   (enc) │
                                       └──────────────────────────────┘
```

- **Static client (GitHub Pages).** Pure HTML/CSS/JS. Loads a local BoardLib CSV
  in the browser, or calls the backend for live data. Ships only the Lambda URL,
  which is not a secret.
- **Lambda (Function URL).** Receives a request, checks the secrets, logs in to
  Tension with the user's credentials, downloads/syncs the *shared* board
  database into `/tmp`, builds the logbook with the `boardlib` library, and
  returns JSON rows. The password is used for one request and never stored.
- **SSM Parameter Store.** Holds the two secrets as KMS-encrypted SecureStrings.
- **Tension API.** The real system of record and the real authority on the
  user's logbook data.

## Request flow

1. **Unlock.** The user types the gate phrase. The page `POST`s
   `{"action":"unlock"}` with header `X-Board-Gate: <phrase>`. The Lambda
   compares it (constant-time) to `/boardlog/gate-phrase`. On success the UI
   reveals; the phrase is kept only in `sessionStorage` for the tab.
2. **Export.** The user supplies the access key + Tension username/password. The
   page `POST`s them with `X-Board-Gate` **and** `X-Board-Room-Key`. The Lambda
   verifies both secrets, then logs in to Tension and returns rows.
3. **Render.** The browser charts the session entirely client-side.

## Why this is secure

The core idea: **the static site is untrusted and holds nothing secret. All
enforcement happens on the server (Lambda), which is the only component the
public cannot read.**

### Threats and the controls that address them

| Threat | Control |
| --- | --- |
| Someone reads the public JS to extract a secret | There are **no secrets in the page**. The gate phrase and access key exist only in the user's memory and in SSM. Client-side "encryption" is intentionally not used because it can't protect a secret the client must also be able to read. |
| Stranger discovers the Function URL and calls it | The handler requires a valid `X-Board-Room-Key`, checked server-side, before doing any work. CORS additionally restricts *browsers* to the GitHub Pages origin. |
| Casual visitor pokes at the page UI | The gate phrase is verified by the Lambda, so the "door" is a real server check, not a cosmetic toggle that DevTools can flip. |
| Timing attack to guess a secret byte-by-byte | Comparisons use `hmac.compare_digest` (constant-time). |
| Secret leaks via infrastructure-as-code | Secrets are SSM SecureStrings created out-of-band, so their plaintext never enters terraform state, the repo, or the Lambda console env vars. They are encrypted at rest with KMS and decrypted only in the Lambda's memory at request time. |
| One secret is compromised | The two secrets are **independent parameters**. Rotating one (`aws ssm put-parameter --overwrite`) does not affect the other and needs no redeploy. |
| Password theft | The Tension password is forwarded to Tension for a single request and never persisted; request-body logging is disabled by convention. The real data authority is Tension's own auth. |
| Abuse / cost / DoS on the public endpoint | Function URL with restrictive CORS plus the required access key; concurrency can be capped in terraform if needed. |

### Trust boundaries

- **Browser → Lambda:** untrusted input. Everything is re-validated server-side
  (method, JSON shape, both secrets, board allow-list, credential presence).
- **Lambda → SSM/KMS:** authorized by a narrowly scoped IAM policy that grants
  `ssm:GetParameter` on exactly the two parameter ARNs and `kms:Decrypt` on the
  SSM-managed key — nothing more.
- **Lambda → Tension:** the Lambda is just a forwarder; Tension is the data
  authority and rejects bad credentials.

### A subtle correctness point that was also a security fix

A public Lambda Function URL (`authorization_type = NONE`) still requires an
explicit resource-based permission (`lambda:InvokeFunctionUrl`, principal `*`).
Without it AWS rejects every invocation with `403 AccessDeniedException` while
the CORS preflight keeps succeeding — which looks like a broken site. The
terraform now declares that permission, so the public surface is exactly the one
intended (POST only, from the configured origin), with auth enforced inside the
function.

**Propagation caveat:** changes to a Function URL's auth type or its invoke
permission can take **several minutes** to reach the Lambda edge. A `403`
immediately after `terraform apply` (or after flipping the auth type) is almost
always propagation, not a misconfiguration — give it a few minutes and re-test
before concluding anything is wrong. (A long detour in this project's history
was caused by mistaking that delay for an account-level block.)

## What this design deliberately does **not** do

- **Per-user identity.** There is one shared gate phrase and one shared access
  key, not individual accounts. For multi-user auth you'd add an identity
  provider (e.g. Cognito) — overkill for a personal tool.
- **Hide the data from the user.** The user authenticates to Tension with their
  own credentials and only ever retrieves their own logbook.
- **Treat the gate phrase as strong cryptographic auth.** It is a real
  server-checked gate, but it travels as a header over TLS and is meant as a
  lightweight "who knows the knock" layer in front of the access key, not as the
  sole protection.
