# AGENTS.md

Guidance for coding agents (Claude Code, Codex, etc.) working in this repository. The user-facing overview is in [README.md](README.md).

## Commands

```bash
uv sync                      # install (Python ≥3.12, deps pinned in uv.lock)
uv run pytest -q             # full suite, about 1 s, no network
uv run pytest tests/test_bot.py -q -k deliver   # one area
uv lock                      # after ANY change to dependencies in pyproject.toml; commit uv.lock
```

Run the CLI against a local config, never against the server's:

```bash
export READLATER_CONFIG=dev/config.toml   # dev/ is git-ignored; copy config.example.toml there
uv run readlater check
```

## Layout

| Path | Responsibility |
|---|---|
| `readlater/cli.py` | argparse subcommands; every command opens the DB through `_db()`, which creates the schema |
| `readlater/config.py` | pydantic models loaded from TOML (`$READLATER_CONFIG`); unknown keys ignored |
| `readlater/store/schema.sql`, `store/db.py` | the only SQL in the codebase: saves, articles, summaries, the job queue, Telegram reply bookkeeping |
| `readlater/dedupe/` | `canonicalize()` (URL dedupe key) and `content_hash()` |
| `readlater/sources/links.py` | finds links and pasted articles in a message, and `item_key()` |
| `readlater/sources/telegram_bot.py` | long-poll loop, message handling, HTML replies, token redaction |
| `readlater/sources/detect.py`, `importer.py` | the one-time email import (mbox, read-only IMAP) |
| `readlater/extractor/fetch.py` | the SSRF-safe fetcher (public IPs only, size cap, per-hop redirect checks) |
| `readlater/extractor/extract.py` | trafilatura extraction, `<link rel=canonical>` |
| `readlater/summarizer/ollama.py` | the structured-output summary; long texts are summarized in parts, then combined |
| `readlater/worker.py` | two loops (extract, summarize) that claim jobs and map errors to queue actions |
| `readlater/export.py` | Markdown rendering for `show` and `export` |
| `readlater/check.py` | `readlater check` |
| `deploy/` | systemd units, `install.sh`, `update.sh` (installed as `readlater-update`), the server runbook |
| `docs/superpowers/plans/` | the implementation plans; the spec is `Email Article Summarizer — Architecture.md` |

## How the pipeline works

1. A save (Telegram, CLI or email) goes through `DB.add_save`. Dedupe happens there:
   - by `(source, source_id)`
   - then by `canonical_url` for links, or `content_hash` for pasted text
2. A new article gets an `extract` job; pasted text skips straight to `summarize`.
3. The worker claims jobs with one `UPDATE … RETURNING`, ordered by `priority, id`. Telegram is 0 and email is 10, so a big import never delays a phone share.
4. `DB.set_extracted` can merge an article into an existing one:
   - when the canonical URL is the same (after redirects or `rel=canonical`)
   - or when the text is the same and the host differs (same-site matches are usually paywall or consent pages, so they're not merged)

   A merge into an article that was never extracted copies the new extraction into it.
5. The bot's `deliver_replies` sends summaries, and failures with their reason, for saves whose `replied_at` is still NULL.

## Conventions (follow these)

- **Test first.** Write the failing test, watch it fail for the right reason, then implement. Bug fixes start with a test that reproduces the bug.
- **No network in tests.** Every outside call takes an injected client (`client: httpx.Client`, `resolve=`, `mailbox_factory=`). Use `httpx.MockTransport` or a small fake class. Never patch `httpx` globally.
- **Errors decide what happens to a job** (`readlater/errors.py`):
  - `TransientError`: a dependency is down (Ollama unreachable, model not pulled). The job is deferred and its attempts aren't counted.
  - `PermanentError`: retrying can't help (blocked address, 404/410, not HTML, no article text). The job fails now.
  - Any other exception is retried with backoff (5, 30, 120 min), 4 attempts in total.
  - Raise the right class at the edge where the error is understood; don't catch broadly in the stages.
- **Time:** INTEGER Unix seconds everywhere in the DB. Functions take a `now: int` argument instead of reading the clock, so tests stay deterministic.
- **Stored errors keep only their first line** (`_first_line`), because they're shown in Telegram.
- **The bot token never appears in logs or exceptions.** `TelegramAPI.call` re-raises as a redacted `TelegramError`, and httpx's INFO logging is silenced in `cli.setup_logging`. Keep it that way.
- **Telegram replies:** summaries use `parse_mode=HTML`. Escape every dynamic value with `html.escape` and keep the text within 4000 UTF-16 code units (`format_summary` drops trailing bullets to fit).
- **Ollama calls:** `stream: false`, `think: false`, `format` = the pydantic JSON schema, `temperature: 0.2`, and an explicit `num_ctx`. Article text always sits inside `<article>` tags, under a system prompt that says it's content, not instructions. The summarizer has no tools.
- **Schema changes:** edit `store/schema.sql` with `CREATE … IF NOT EXISTS`; it runs on every start. There's no migration framework yet, so adding a column to an existing table needs a deliberate migration step. Raise it first; don't do it silently.
- **Style:** match the surrounding code: short module docstrings, comments only where the reason isn't obvious, no new abstractions without a second user.

## Public repository: keep it clean

This repo is public on GitHub, and the server deploys straight from it.

- **Never commit** hostnames, Tailscale names or IPs, timezones, email addresses, Telegram IDs, tokens or a real config. Committed files use placeholders: `<ollama-host>`, `<node-a>`, `<owner>/<repo>`, `<Region/City>`. Real values live only in `dev/` (git-ignored) or `/etc/readlater/` on the server.
- `.gitignore` blocks `*.db`, `*.mbox`, `*.eml`, `*.secret`, `.env` and `config.toml`. Don't weaken it.
- **Commits** use the GitHub no-reply email configured in this repo's `.git/config`.
- **Before any push,** scan what will be published, e.g. `git log -p origin/main..HEAD | grep -iE '<your hostnames>|@gmail|[0-9]{8,10}:AA'`.
- **Never force-push `main`:** a GitHub ruleset blocks it.

## Deployment notes

- **Deps:** `deploy/update.sh` runs `uv sync --locked`, so a `pyproject.toml` change without `uv lock` fails the deploy and rolls back. Always commit `uv.lock` with dependency changes.
- **Hardening:** the units run with `ProtectSystem=strict`, so the app directory is read-only at runtime. Only `/srv/readlater/data` is writable, and `PYTHONDONTWRITEBYTECODE=1` is set. Don't write anywhere else at runtime.
- **One bot per token:** only one process may call Telegram's `getUpdates` for a token. Don't run the bot locally while the server's runs.
- **Scripts:** `install.sh` and `update.sh` must stay idempotent and must never touch `/srv/readlater/data`, `/etc/readlater/config.toml` or the token. `tests/test_deploy_files.py` checks this; test script changes in a `debian:trixie` container (`READLATER_NO_SYSTEMD=1` skips systemctl).

## Known gaps

These are deliberately deferred, so don't "fix" them in passing; propose them first:
- Untracked files in the server checkout aren't detected by `readlater-update`.
- Two `readlater-update` runs at the same time aren't locked out.
- The server's Python version isn't pinned.
- There's no web UI or search yet (planned: FastAPI + HTMX, FTS5, `sqlite-vec` embeddings).
- PDFs and JavaScript-only pages fail with a clear reason instead of being extracted.
