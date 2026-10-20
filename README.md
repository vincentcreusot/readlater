# readlater

A private read-later archive that runs on your own machines. Share a link with a Telegram bot from your phone. readlater fetches the article, strips it down to the readable text, summarizes it with a local LLM through [Ollama](https://ollama.com), and replies with the summary. Everything is kept in one SQLite file you can export as Markdown.

No cloud LLM is involved. The only outside connections are the bot talking to Telegram and the article downloads.

```
phone ──share link──▶ Telegram ◀──long poll── readlater bot ─┐
                                                              ├── SQLite (saves, articles, summaries, jobs)
                        Ollama (LAN/Tailscale) ◀── readlater worker ─┘   extract → summarize
```

## What it does

- **Saves from Telegram.** Share a link, or paste the text of an article. The bot answers "Saved" right away and replies with a formatted summary (title, 2-sentence TL;DR, key points, link) a few minutes later. It ignores everyone except the Telegram accounts you allow.
- **Deduplicates.** The same article saved twice, through tracking links, `www.` and `http` variants, redirects or short links, is summarized once. You get "Already saved on …" with the existing TL;DR.
- **Handles long articles.** Anything over about 48,000 characters is summarized in parts, which are then combined, so a long essay isn't cut off.
- **Fetches safely.** Only public addresses (a link can't make it probe your LAN), a 5 MB size cap, a limit on redirects, and an honest User-Agent.
- **Copes with outages.** If Ollama is down, jobs wait instead of failing. Real failures (a PDF, a page with no article text, a 404) are reported back in Telegram with the reason. Send `/retry` to try again.
- **Imports old emails once.** Old emails you sent yourself with a link and no subject can be imported from an mbox export, or read-only over IMAP.
- **Exports to Markdown.** One file per article with YAML frontmatter, ready for Obsidian or any editor.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Ollama on a machine you can reach, with a model pulled (default `qwen3.5:4b`; CPU-only works). Expect about 3–4 minutes per article part on a CPU-only Tiny PC.
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Quick start (local)

```bash
git clone https://github.com/vincentcreusot/readlater.git && cd readlater
uv sync

mkdir -p dev && cp config.example.toml dev/config.toml   # dev/ is git-ignored
# edit dev/config.toml: [ollama] url, [paths] data = "./dev/data"
export READLATER_CONFIG=dev/config.toml

uv run readlater check                 # config, data dir, Ollama, bot token
uv run readlater add https://www.paulgraham.com/greatwork.html
uv run readlater worker --once         # extract and summarize, then exit
uv run readlater show 1                # the summary as Markdown
```

To use the Telegram bot:

```bash
export READLATER_TELEGRAM_TOKEN='123456:ABC...'
uv run readlater bot        # terminal 1
uv run readlater worker     # terminal 2
```

Send the bot a message. Its log prints `ignoring message from user id N`. Put `N` in `allowed_user_ids` and restart the bot. Only one process may run the bot per token.

## Commands

| Command | What it does |
|---|---|
| `readlater check` | Checks the config, data folder, Ollama, the model and the bot token. Every problem is named on its own line |
| `readlater add <url-or-text>` | Saves a link or pasted text from the command line |
| `readlater worker [--once]` | Runs the extract and summarize queue (`--once`: until it's empty) |
| `readlater bot` | Runs the Telegram bot |
| `readlater show <id> [--full]` | Prints a summary as Markdown (`--full`: adds frontmatter and the article text) |
| `readlater export <dir>` | Writes every summarized article to `<dir>` as Markdown; safe to re-run |
| `readlater retry` | Re-queues failed jobs (also `/retry` in the bot) |
| `readlater import-mail --mbox FILE [--dry-run]` | One-time import of old self-sent emails; without `--mbox` it reads the IMAP folder from the `[mail]` config section |
| `readlater init-db` | Creates the database (optional: every command creates it when missing) |

In the bot: share a link, paste text over 300 words, or use `/recent`, `/retry` and `/start`.

## Configuration

Settings live in a TOML file named by `READLATER_CONFIG`. Start from [`config.example.toml`](config.example.toml) for local use, or [`deploy/config.server.toml`](deploy/config.server.toml) for a server.

| Section | Keys |
|---|---|
| `[telegram]` | `allowed_user_ids`: the only accounts the bot answers |
| `[ollama]` | `url`, `model` (default `qwen3.5:4b`), `num_ctx` (default 16384) |
| `[fetch]` | `contact`: optional email or URL added to the User-Agent. Wikipedia refuses bots without one |
| `[mail]` | `own_addresses` (used to recognize your self-sent emails); `host`, `user`, `folder` only for IMAP import |
| `[paths]` | `data`: the folder that holds `readlater.db` |

The bot token is never in the config. Locally it comes from `READLATER_TELEGRAM_TOKEN`; on a server, from a systemd credential.

## Deploying

[`deploy/README.md`](deploy/README.md) is the runbook for an always-on Debian 13 server. Two hardened systemd services run as an unprivileged user; `install.sh` sets the server up once, and `sudo readlater-update` pulls a new release from this repo. Updates install locked dependencies and roll back automatically if that fails.

## Development

```bash
uv run pytest -q
```

The tests never touch the network: every HTTP call (pages, Ollama, Telegram, IMAP) goes through an injected client, which the tests replace with fakes. [`AGENTS.md`](AGENTS.md) covers the code layout and conventions.

Design documents:
- [`Email Article Summarizer — Architecture.md`](Email%20Article%20Summarizer%20—%20Architecture.md): the original design.
- [`docs/superpowers/plans/`](docs/superpowers/plans/): the implementation plans.

## Status

Working: the bot, the worker, dedupe, long articles, the email import, the Markdown export, and deployment.

Not built yet: a web UI with keyword and semantic search, the weekly digest, and fallbacks for JavaScript-heavy pages and PDFs.

## License

[MIT](LICENSE)
