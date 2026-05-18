# anyframe-discord-bot

A drop-in Discord bot powered by [anyframe](https://anyfrm.com). @mention
the bot in any channel and it spawns a thread and a private agent
sandbox — each thread runs in its own isolated session.

```
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │   Discord    │──▶│  this bot    │──▶│  sandbox 1   │
 │  (your bot)  │   │ + API key    │   │  sandbox 2   │
 │              │   │ + sessions   │   │  sandbox …   │
 └──────────────┘   └──────────────┘   └──────────────┘
                                       one per thread on anyfrm.com,
                                       snapshot + resume across evictions
```

Each thread maps to its own sandbox, snapshotted between turns so
evictions are invisible. Your `afm_…` key stays on your server; the
Discord side only sees rendered events.

## Quick start

### 1. Create your agent

[anyfrm.com](https://anyfrm.com) → **New agent**. Pick a harness
(Claude or Codex), set the system prompt, optionally point it at a
repo. Note the id from the URL (`/agents/123`).

### 2. Get an API key

**Settings → API keys → Create**. Copy the `afm_…` value.

### 3. Create the Discord app

Discord Developer Portal → **New Application** → **Bot** tab → reset
token and copy it. On the same page, enable **Message Content Intent**
under *Privileged Gateway Intents*.

Then **OAuth2 → URL Generator**. Scopes: `bot`. Permissions:

- Send Messages
- Read Message History
- Create Public Threads
- Send Messages in Threads

Open the generated URL and invite the bot to your server.

### 4. Deploy this bot

Fork the repo, push to [Railway](https://railway.com) (or any Docker
host). Set four env vars:

```
ANYFRAME_API_KEY=afm_…
ANYFRAME_AGENT_ID=123
DISCORD_BOT_TOKEN=…
```

Mount a `/data` volume so the thread → session mapping persists across
redeploys. The bot connects to Discord on boot — watch the logs for
`logged in as <bot-name>`.

## How it works

| Trigger | What the bot does |
| --- | --- |
| `@bot <prompt>` in a channel | Creates a thread, opens a fresh anyframe session, sends the prompt, streams events back. |
| Any message inside that thread | Forwarded to the same session. No mention needed. |
| Message in a thread after the sandbox evicted | Calls `resume` — boots from the latest snapshot. |
| Message in a thread with no snapshot to resume from | Posts a notice and creates a fresh session. |

State (`thread_id → session_id, last_seq`) lives in a SQLite file at
`$STATE_DB_PATH` (default `/data/state.db`). `last_seq` is used as the
SSE `Last-Event-ID` so the bot only renders events from the current
turn, not the entire session history.

## Customize

**The agent** — what it knows and can do — lives on anyfrm.com:

- **Harness** — Claude or Codex today; BYO harness landing soon.
- **System prompt** — set the agent's persona and instructions.
- **Skills** — custom playbooks you add per agent.
- **MCPs + connectors** — Linear, GitHub, Slack, etc. for tool access.
- **Repo** (optional) — clone a codebase into the sandbox if the
  agent needs it.

Edit on the agent page; threads pick up changes on their next turn.

**The bot** — how events render in Discord — is a small Python
package. No build step:

- `app/events.py` — how assistant text and tool calls are rendered.
- `app/bot.py` — thread lifecycle (mention → thread, reply → message).
- `app/config.py` — env-driven knobs (limits, timeouts).

Edit, redeploy.

## Settings reference

The only ones you must set:

| Variable | What it is |
| --- | --- |
| `ANYFRAME_API_KEY` | The `afm_…` key from anyframe Settings. |
| `ANYFRAME_AGENT_ID` | The number after `/agents/` in your agent's URL. |
| `DISCORD_BOT_TOKEN` | The bot token from the Discord Developer Portal. |

Recommended:

| Variable | What it is |
| --- | --- |
| `STATE_DB_PATH` | Where the SQLite file lives. Default `/data/state.db`. |

Optional tuning (defaults are sensible):

| Variable | What it does |
| --- | --- |
| `ANYFRAME_BASE_URL` | AnyFrame control plane. Default `https://api.anyfrm.com`. |
| `BOOT_TIMEOUT_S` | How long to wait for a sandbox to reach `running`. Default 180. |
| `DISCORD_MSG_LIMIT` | Chars per Discord message before splitting. Default 1900. |
| `THREAD_NAME_LIMIT` | Max chars used from the opening prompt as a thread title. Default 80. |
| `THREAD_AUTO_ARCHIVE_MINUTES` | Discord thread auto-archive (60, 1440, 4320, or 10080). Default 10080. |

See `.env.example` for everything.

## For developers

<details>
<summary>Local dev, layout, single-replica notes</summary>

### Local dev

```bash
uv sync
cp .env.example .env  # fill in ANYFRAME_*, DISCORD_BOT_TOKEN
uv run python -m app.main
```

You should see `logged in as <bot> (...)` in the logs once the
gateway handshake completes. Mention the bot in a server it's been
invited to and a thread will spawn.

### Layout

```
app/
  config.py     — pydantic-settings; one Settings singleton
  state.py      — SQLite mapping thread_id → (session_id, last_seq)
  sessions.py   — AsyncAnyFrame client; ensure_session() boots / resumes
  events.py     — render assistant + tool_use blocks for Discord
  bot.py        — discord.Client wiring: on_message → ensure_session → stream
  main.py       — entrypoint (`python -m app.main`)
```

### Notes

- **Single replica only.** `discord.py` holds a single gateway
  connection per bot token. Don't scale horizontally.
- **State is local.** SQLite at `$STATE_DB_PATH`. Mount a Railway
  Volume (or any persistent disk) at `/data` so the thread → session
  mapping survives redeploys.

</details>

