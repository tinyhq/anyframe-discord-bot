# anyframe-discord-bot

Discord bot that drives an [anyframe](https://anyfrm.com) agent. @mention
the bot in any channel and it spawns a thread, boots a sandbox, and
streams the agent's replies back. Subsequent messages in the thread
continue the same session — resuming from the latest snapshot if the
sandbox has been evicted.

## How it works

| Trigger | What the bot does |
| --- | --- |
| `@bot <prompt>` in a channel | Creates a thread, creates a new anyframe session, sends `prompt` to it, streams events back. |
| Any message inside that thread | Forwarded to the same session. No mention needed. |
| Message in a thread after the sandbox evicted | Calls `POST /api/sessions/{id}/resume` — boots from latest snapshot. |
| Message in a thread with no snapshot to resume from | Posts a notice and creates a fresh session. |

State (`thread_id → session_id, last_seq`) lives in a SQLite file at
`$STATE_DB_PATH` (default `/data/state.db`). `last_seq` is used as the
SSE `Last-Event-ID` so the bot only renders events from the current turn,
not the entire session history.

## Environment

| Variable | Purpose |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Discord application bot token. Must have **Message Content Intent** enabled. |
| `ANYFRAME_BASE_URL` | URL of the anyframe API (e.g. `https://api.anyfrm.com`). |
| `ANYFRAME_API_TOKEN` | `afm_…` personal token from `/settings/tokens` on the anyframe dashboard. |
| `ANYFRAME_AGENT_ID` | Numeric id of the agent the bot drives. |
| `STATE_DB_PATH` | Optional. Path to the SQLite file. Default `/data/state.db`. |

## Discord setup (one-time)

1. Discord Developer Portal → New Application → Bot tab → reset token, copy it.
2. Same page: enable **Message Content Intent** under *Privileged Gateway Intents*.
3. OAuth2 → URL Generator. Scopes: `bot`. Permissions:
   - Send Messages
   - Read Message History
   - Create Public Threads
   - Send Messages in Threads
4. Open the generated URL and invite the bot to your server.

## Deploy on Railway

1. Create a new Railway project (or reuse an existing one).
2. **Add Service → Deploy from GitHub repo** → pick this repo.
3. In the service's *Settings*:
   - **Root Directory**: `/` (default)
   - **Config Path**: `railway.toml`
4. **Variables** tab → set the four env vars above.
5. **Volumes** tab → mount a small (≥1 GB) volume at `/data`. This persists
   the SQLite state across redeploys.
6. Deploy. The bot should come online in Discord within a minute. Watch
   *Logs* for `logged in as <bot-name> (...)`.

To update env vars later, edit them in the Variables tab and Railway
redeploys automatically.

## Local development

```bash
uv sync
export DISCORD_BOT_TOKEN=…
export ANYFRAME_BASE_URL=http://localhost:8000
export ANYFRAME_API_TOKEN=afm_…
export ANYFRAME_AGENT_ID=4
export STATE_DB_PATH=./state.db
uv run python -c "import bot; bot.main()"
```

## Integrating as a submodule

This repo is designed to be embedded into the
[`tinyhq/box`](https://github.com/tinyhq/box) repo as a submodule at
`examples/discord-bot`:

```bash
# from inside box/
git submodule add git@github.com:tinyhq/box-discord-bot.git examples/discord-bot
git commit -m "chore: add discord bot as submodule"
```

Cloners of `box` then run:

```bash
git submodule update --init --recursive
```
