# Configurable WhatsApp Chatbot

*[Versão em português](README.md)*

A WhatsApp support bot whose content lives in a YAML file rather than in code.
To serve a different company, swap the file — no Python changes.

Built with FastAPI, Redis and [WAHA](https://waha.devlike.pro) as the WhatsApp
provider.

```yaml
states:
  menu:
    text: |
      Hi! Welcome to *Acme* support.
      1️⃣ Technical support
      2️⃣ Sales
    options:
      "1":
        text: "Describe your problem. An engineer will take over."
        next: handoff
      "2":
        text: "Catalogue: https://example.com/products"
        next: menu
```

## Why separate flow from code

The earlier version of this project kept the menu, the copy and the transitions
inside an `if/elif` chain in `main.py`. It worked for one company, and only one.

Splitting the two solves more than reuse:

- **Content no longer needs a programmer.** Whoever owns support edits the YAML
- **The conditional chain disappears**, and with it the chance of two paths drifting apart
- **The engine becomes testable** with no WhatsApp, no Redis and no network
- **Flow mistakes surface at startup**, not in a customer's conversation

## Getting started

```bash
git clone <your-repository>
cd whatsapp-chatbot

cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # paste into WEBHOOK_SECRET

docker compose up -d --build
```

Then, in the WAHA dashboard (http://127.0.0.1:3000):

1. Scan the QR code to start the session
2. Point the webhook at `http://bot:8000/webhook`
3. Under **Events**, leave only `message` enabled
4. Add an `X-Webhook-Token` header with the same value as `WEBHOOK_SECRET`

To build your own flow, copy `flows/example.yml`, edit it, and point `FLOW_PATH`
at the copy.

## Flow format

### `bot`

| Field | Purpose |
| --- | --- |
| `name` | Bot name, used in logs |
| `session` | WAHA session name |
| `entrypoint` | State every new user starts in |
| `state_ttl` | Seconds before a user's state expires (60 to 86400) |
| `reset_keywords` | Words that return to the start from anywhere |
| `back_hint` | Appended to replies, except in the entrypoint state |
| `fallback` | Reply when nothing matches |
| `unsupported_media` | Reply when audio, images or stickers arrive |

### `states`

Each state is a point in the conversation. On entering a state the bot sends its
`text` and starts accepting the keys under `options`.

```yaml
state_name:
  text: "Message sent when entering here"
  options:
    "1":
      label: "Optional label, documentation only"
      text: "Reply to this choice"
      next: another_state     # empty keeps the user where they are
```

### `handoff`

`next: handoff` hands the conversation to a human agent: the bot goes silent and
stops replying to anything, media included. The only way out is a
`reset_keyword`, which stops the bot from interrupting an ongoing conversation.

`handoff` is built in and is not declared under `states`. To give that behaviour
a different name, create a state with `handoff: true`.

### Validation

The flow is validated when the container starts. Rejected: a `next` pointing at a
state that does not exist, a missing `entrypoint`, unreachable states, unknown
fields, empty `reset_keywords`, and redefining reserved names.

A typo stops the container from starting, instead of leading a customer into a
dead end.

## Security

**The webhook requires authentication.** Without `WEBHOOK_SECRET` the application
refuses to start. That is deliberate: an open webhook lets anyone who can reach
the port make the connected number send messages to any destination.

Two proofs of origin are accepted:

| Mechanism | Header | Guarantees |
| --- | --- | --- |
| Shared token | `X-Webhook-Token` | The request knows the secret |
| HMAC-SHA256 of the body | `X-Webhook-Hmac` | Origin **and** that the body was not altered |

Comparison uses `compare_digest`, so response timing does not reveal how many
characters of the secret were guessed correctly.

**Other measures:**

- No message content is persisted. Redis holds only a state name and a counter,
  both with expiry
- Phone numbers appear in logs as a short hash, never in the clear
- Per-user message rate limit over a time window
- Payload validated with Pydantic: unexpected shapes become 422, not 500
- Redis unavailable becomes 503 so the provider retries — no stack trace in the response
- The container runs as an unprivileged user with read-only application code
- Redis publishes no port; WAHA publishes only on `127.0.0.1`
- `.gitignore` and `.dockerignore` both cover `.env` and `sessions/`

About `sessions/`: that folder holds the WhatsApp authentication state. Whoever
obtains it can impersonate the connected number. It must never be committed or
baked into an image.

## Architecture

```
WhatsApp  ──►  WAHA  ──►  POST /webhook
                             │
                     security.verify()      token or HMAC
                             │
                     schemas.WebhookEvent   payload validation
                             │
                     state.StateStore       state + rate limit (Redis)
                             │
                     engine.decide()        pure function: state + text → reply
                             │
                     providers.waha         delivery
```

| File | Responsibility |
| --- | --- |
| `app/main.py` | HTTP, authentication and wiring. No conversation rules |
| `app/engine.py` | The state machine. Pure function, no I/O |
| `app/flow.py` | Loading and validating the YAML |
| `app/schemas.py` | Shape of the incoming webhook |
| `app/security.py` | Token and HMAC verification |
| `app/state.py` | Per-user state and rate limiting, in Redis |
| `app/providers/` | Provider abstraction; `waha.py` is the current implementation |
| `app/config.py` | Environment configuration, fail-closed |

Swapping WAHA for Twilio or Meta's Cloud API means writing another class with
`send_text` and `close`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

62 tests, with no WhatsApp and no real Redis. They cover flow validation, the
state machine, webhook authentication, the payloads that used to crash the
service, the rate limiter and Redis going down.

## Known limitations

- **Text only.** Incoming media gets an explanatory reply; the bot does not
  process images, audio or location
- **No admin UI.** Editing the flow means editing the YAML and restarting
- **No metrics.** There is `/health`, but nothing exported to Prometheus
- **Fixed-window rate limiter.** Simple and sufficient here; a burst straddling
  the window boundary gets through
- **One provider implemented.** The abstraction exists, but only WAHA is written

## License

MIT
