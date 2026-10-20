# readlater Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the bot and worker on the always-on server (node A) as systemd services that start at boot and survive crashes, talking to Ollama on node B over Tailscale, with a one-command way to ship new code published on GitHub.

**Architecture:** Node A runs one git checkout at `/srv/readlater/app` with a uv-managed virtualenv. It runs two systemd units (`readlater-bot`, `readlater-worker`) as the unprivileged `readlater` user, sharing `/srv/readlater/data/readlater.db`. Code is published to a public GitHub repo. Node A clones it read-only over HTTPS with no credentials, so the server can never push. `readlater-update` (installed from `deploy/update.sh`) fetches the new commit, syncs dependencies and restarts the units. Node B stays the inference box, serving Ollama on `0.0.0.0:11434` and reachable over Tailscale as `<ollama-host>`.

**Tech Stack:** GitHub (public repo), Debian 13, systemd (`LoadCredential=`, hardening options), uv, git, Tailscale, plus Docker on the dev machine to test the install scripts in a `debian:trixie` container.

**Spec:** `Email Article Summarizer — Architecture.md`, sections "System architecture", "Tech stack and Debian setup" and "Implementing readlater". It builds on the core plan `docs/superpowers/plans/2026-10-20-readlater-core.md`.

**Assumptions** (the user didn't answer the setup questions; change these before executing):
1. **Node A** is a second Tiny PC running Debian 13, reachable over ssh and Tailscale as `<node-a>`. If node B should run everything instead (the spec's single-box fallback), use `<node-a>` = `<ollama-host>` and `[ollama] url = "http://localhost:11434"`. Nothing else changes.
2. **Code** lives in a public GitHub repo, written `https://github.com/<owner>/<repo>.git` below. You push from the dev machine; node A only pulls. Nothing secret or machine-specific is committed: the real config and the token exist only under `/etc/readlater/` on node A.
3. **No disk encryption.** The spec asks for LUKS on node A, but the user dropped backups and treats the LAN as trusted. Adding LUKS later would mean reinstalling with TPM2 auto-unlock (`systemd-cryptenroll --tpm2-device=auto`), otherwise the "comes back after power loss" requirement fails.

**Out of scope:** the web UI (its own plan), backups (dropped by the user), the digest timer, LUKS (see assumption 3).

## Global Constraints

- **Paths (from the spec):**
  - App checkout: `/srv/readlater/app`
  - Virtualenv: `/srv/readlater/app/.venv`
  - Data: `/srv/readlater/data`
  - Config: `/etc/readlater/config.toml`
  - Bot token: `/etc/readlater/telegram.secret`, mode 600, owned by root
- **Repo URL:** written once by `install.sh` to `/etc/readlater/repo-url`. `readlater-update` only ever fetches from it.
- **Nothing machine-specific in the repo:** committed files use placeholders (`<ollama-host>`, `<node-a>`, `<owner>/<repo>`). Real values go only into `/etc/readlater/config.toml` on node A.
- **Service user:** system user `readlater` with home `/srv/readlater`.
- **Units:** `readlater-bot.service` and `readlater-worker.service`. Both have `User=readlater`, `Environment=READLATER_CONFIG=/etc/readlater/config.toml`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes` and `ReadWritePaths=/srv/readlater/data`, and are wanted by `multi-user.target`.
  - Bot: `LoadCredential=telegram:/etc/readlater/telegram.secret`, `Restart=always`, `RestartSec=15`.
  - Worker: `Restart=always`, `RestartSec=30`.
  - The spec's worker had `Restart=on-failure`. Here the worker's loops never exit on their own, so `always` is the safer choice.
- **Exactly one bot process per token** (a Telegram rule): never run `readlater bot` on the dev machine while the server unit is active.
- **Ollama URL on node A:** `http://<ollama-host>:11434`, node B's Tailscale MagicDNS name. Model: `qwen3.5:4b`.
- **Server timezone:** your local timezone (`timedatectl set-timezone <Region/City>`), because "Saved on" dates use local time.
- **Scripts:** `deploy/install.sh` and `deploy/update.sh` run as root, are idempotent (safe to run twice) and use `set -euo pipefail`. A dry container run must pass with `READLATER_NO_SYSTEMD=1`, which skips `systemctl` calls.
- **Commits:** every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Fresh server with no `init-db` run:** the bot or worker must create the schema itself, not crash-loop with "no such table" (Task 1 `test_worker_run_creates_schema`, `test_commands_work_without_init_db`).
2. **Node B down or Tailscale not up yet at boot:** the worker must start, log the deferral and recover once node B answers, with no restart storm (manual check in Task 5, step 6).
3. **A wrong or placeholder setting** (token, `allowed_user_ids = [123456789]`, a model that isn't pulled): `readlater check` must name the exact problem before you go looking in the logs (Task 2).
4. **Re-running `install.sh` or `update.sh`** must not wipe `/srv/readlater/data`, the config or the token (Task 4 `container: rerun keeps data`).
5. **A push that breaks dependencies:** `update.sh` must fail before restarting, so the running services keep the old code (Task 4, `uv sync` runs before `systemctl restart`, and `set -e`).

---

## File Structure

```text
readlater/check.py             readlater check: config, data dir, Ollama, Telegram
readlater/cli.py               (modify) auto-create schema; check subcommand
readlater/worker.py            (modify) run() creates the schema
deploy/config.server.toml      server config template, copied to /etc/readlater/config.toml
deploy/systemd/readlater-bot.service
deploy/systemd/readlater-worker.service
deploy/install.sh              one-time server setup (user, dirs, clone, uv, units)
deploy/update.sh               check out pushed code, uv sync, restart
deploy/README.md               the runbook: first install, shipping updates, logs
tests/test_check.py
tests/test_deploy_files.py     unit files and scripts agree on paths and commands
```

---

### Task 1: Create the schema automatically

**Files:**
- Modify: `readlater/cli.py` (`_db`), `readlater/worker.py` (`run`)
- Test: `tests/test_cli.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: `DB.init_schema()` from the core plan, Task 4. It's idempotent (`CREATE TABLE IF NOT EXISTS`).
- Produces: every CLI command, and `worker.run`, works on an empty data directory.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
def test_commands_work_without_init_db(tmp_path, monkeypatch, capsys):
    cfg = Path("config.example.toml").read_text().replace('data = "./data"', f'data = "{tmp_path}"')
    (tmp_path / "c.toml").write_text(cfg)
    monkeypatch.setenv("READLATER_CONFIG", str(tmp_path / "c.toml"))
    assert main(["retry"]) == 0
    assert "re-queued 0 failed jobs" in capsys.readouterr().out

# tests/test_worker.py
def test_worker_run_creates_schema(tmp_path):
    import threading
    cfg = load_config(Path("config.example.toml")); cfg.paths.data = tmp_path
    worker.run(cfg, threading.Event(), once=True)        # must not raise "no such table"
```

- [ ] **Step 2:** Run `uv run pytest tests/test_cli.py tests/test_worker.py -q`. Expected: 2 failed with `sqlite3.OperationalError: no such table`.
- [ ] **Step 3:** In `cli._db`, call `init_schema()` before returning the DB. In `worker.run`, call `db.init_schema()` before `reset_running()`.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all passed.
- [ ] **Step 5: Commit** with message `fix: create the schema on first use`.

---

### Task 2: `readlater check`

**Files:**
- Create: `readlater/check.py`
- Modify: `readlater/cli.py` (the `check` subcommand)
- Test: `tests/test_check.py`

**Interfaces:**
- Consumes: `Config` (the core plan's Task 1), `TelegramAPI` and `load_token` (core Task 10).
- Produces:
  - `CheckResult(name: str, ok: bool, detail: str)`, a frozen dataclass.
  - `PLACEHOLDER_USER_ID = 123456789`
  - `check_config(cfg) -> list[CheckResult]`:
    - `allowed users` fails when the list is empty or contains `PLACEHOLDER_USER_ID`, with detail `"set [telegram] allowed_user_ids to your numeric Telegram ID"`.
    - `data dir` fails when `cfg.paths.data` can't be created or written to.
  - `check_ollama(cfg, client: httpx.Client) -> CheckResult` calls `GET {url}/api/tags` (10 s timeout).
    - Not reachable: fails with detail `"cannot reach <url>: <error first line>"`.
    - Model missing: fails with detail `"model <model> not pulled; run: ollama pull <model>"`. A model counts as present when the tag list contains `<model>` or `<model>:latest`.
  - `check_telegram(token: str | None, client: httpx.Client) -> CheckResult` calls `getMe`.
    - Passes with detail `"@<username>"`.
    - Fails with `"no token: …"` (the `load_token` message) when the token is missing, or with the redacted Telegram error.
  - `run_checks(cfg, client, token_loader=load_token) -> list[CheckResult]`.
  - `cli check`: prints one line per result (`ok   <name>: <detail>` or `FAIL <name>: <detail>`) and returns 0 only if all pass.

- [ ] **Step 1: Write the failing test**

```python
CFG = lambda tmp: load_config(Path("config.example.toml")).model_copy(update={"paths": PathsCfg(data=tmp)})

def tags(*names):
    return httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"models": [{"name": n} for n in names]})))

def test_placeholder_user_id_fails(tmp_path):
    r = {c.name: c for c in check_config(CFG(tmp_path))}
    assert not r["allowed users"].ok and "allowed_user_ids" in r["allowed users"].detail
    assert r["data dir"].ok

def test_ollama_ok_and_missing_model(tmp_path):
    assert check_ollama(CFG(tmp_path), tags("qwen3.5:4b", "nomic-embed-text:latest")).ok
    bad = check_ollama(CFG(tmp_path), tags("llama3:8b"))
    assert not bad.ok and bad.detail == "model qwen3.5:4b not pulled; run: ollama pull qwen3.5:4b"

def test_ollama_unreachable(tmp_path):
    def h(r): raise httpx.ConnectError("Connection refused")
    res = check_ollama(CFG(tmp_path), httpx.Client(transport=httpx.MockTransport(h)))
    assert not res.ok and res.detail.startswith("cannot reach http://192.168.1.21:11434")

def test_telegram_ok_and_bad_token():
    ok = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok": True, "result": {"username": "my_reader_bot"}})))
    assert check_telegram("1:T", ok) == CheckResult("telegram", True, "@my_reader_bot")
    bad = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"})))
    res = check_telegram("1:SECRET", bad)
    assert not res.ok and "Unauthorized" in res.detail and "SECRET" not in res.detail

def test_telegram_missing_token():
    assert not check_telegram(None, httpx.Client()).ok

def test_cli_check_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("readlater.check.run_checks", lambda cfg, client, **kw: [CheckResult("x", False, "bad")])
    cfg = Path("config.example.toml").read_text().replace('data = "./data"', f'data = "{tmp_path}"')
    (tmp_path / "c.toml").write_text(cfg); monkeypatch.setenv("READLATER_CONFIG", str(tmp_path / "c.toml"))
    assert main(["check"]) == 1 and "FAIL x: bad" in capsys.readouterr().out
```

- [ ] **Step 2:** Run `uv run pytest tests/test_check.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 3:** Implement `check.py` and `cli check`. `run_checks` catches the token loader's `RuntimeError` and passes `None` to `check_telegram`.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all passed.
- [ ] **Step 5: Manual check** with `READLATER_CONFIG=dev/config.toml uv run readlater check`. Expected:
  - `ok ollama: qwen3.5:4b on http://<ollama-host>:11434`
  - `FAIL allowed users: …` (the dev config still has the placeholder)
  - `FAIL telegram: no token …` unless `READLATER_TELEGRAM_TOKEN` is set
- [ ] **Step 6: Commit** with message `feat: readlater check for config, ollama and telegram`.

---

### Task 3: systemd units and server config

**Files:**
- Create: `deploy/systemd/readlater-bot.service`, `deploy/systemd/readlater-worker.service`, `deploy/config.server.toml`
- Test: `tests/test_deploy_files.py`

**Interfaces:**
- Produces: unit files with exactly the Global Constraints values.
  - `[Unit]`: `After=network-online.target tailscaled.service` and `Wants=network-online.target`.
  - `[Service]`: `Environment=PYTHONDONTWRITEBYTECODE=1`, because the app directory is read-only under `ProtectSystem=strict`.
  - `ExecStart`: `/srv/readlater/app/.venv/bin/readlater bot` or `… worker`.
  - `config.server.toml` is `config.example.toml` with these changes:
    - `[ollama] url = "http://<ollama-host>:11434"`, a placeholder that `readlater check` reports as unreachable until it's replaced
    - `[paths] data = "/srv/readlater/data"`
    - `allowed_user_ids = [123456789]` left as the placeholder, commented "replace with your ID (readlater check tells you)"
    - the `[mail]` section commented out, because the import runs from an mbox

- [ ] **Step 1: Write the failing test**

```python
import configparser, re, tomllib
from pathlib import Path
from readlater.cli import build_parser

UNITS = {"bot": Path("deploy/systemd/readlater-bot.service"), "worker": Path("deploy/systemd/readlater-worker.service")}

def unit(path):
    p = configparser.ConfigParser(strict=False, interpolation=None); p.optionxform = str
    p.read(path); return p

def test_units_run_real_subcommands_as_readlater():
    commands = set(build_parser()._subparsers._group_actions[0].choices)
    for name, path in UNITS.items():
        s = unit(path)["Service"]
        assert s["ExecStart"] == f"/srv/readlater/app/.venv/bin/readlater {name}" and name in commands
        assert s["User"] == "readlater" and s["Restart"] == "always"
        assert s["ReadWritePaths"] == "/srv/readlater/data" and s["ProtectSystem"] == "strict"
        assert "READLATER_CONFIG=/etc/readlater/config.toml" in s["Environment"]

def test_only_bot_gets_the_token():
    assert unit(UNITS["bot"])["Service"]["LoadCredential"] == "telegram:/etc/readlater/telegram.secret"
    assert "LoadCredential" not in unit(UNITS["worker"])["Service"]

def test_server_config_parses_and_points_at_server_paths():
    cfg = tomllib.loads(Path("deploy/config.server.toml").read_text())
    assert cfg["ollama"]["url"] == "http://<ollama-host>:11434" and cfg["paths"]["data"] == "/srv/readlater/data"
```

- [ ] **Step 2:** Run `uv run pytest tests/test_deploy_files.py -q`. Expected: FAIL (the files don't exist).
- [ ] **Step 3:** Write the two unit files and `config.server.toml`.
- [ ] **Step 4:** Run `uv run pytest -q` and `systemd-analyze verify deploy/systemd/*.service`. Expected: all tests pass. `systemd-analyze` may warn only that `/srv/readlater/app/.venv/bin/readlater` doesn't exist on the dev machine; any other warning is a defect.
- [ ] **Step 5: Commit** with message `feat: systemd units and server config`.

---

### Task 4: install.sh, update.sh and the runbook

**Files:**
- Create: `deploy/install.sh`, `deploy/update.sh`, `deploy/README.md`
- Modify: `tests/test_deploy_files.py` (the scripts reference the same paths as the units)
- Verify: a `debian:trixie` container run (commands below)

**Interfaces:**
- `install.sh`, run as root on node A from a checkout or via `curl`/`scp`. Steps:
  1. `apt-get install -y git sqlite3 curl ca-certificates`
  2. `useradd --system --create-home --home-dir /srv/readlater --shell /usr/sbin/nologin readlater`, skipped if the user exists
  3. `install -d -o readlater -g readlater /srv/readlater/data`
  4. `install -d -m 755 /etc/readlater`
  5. Install uv system-wide with `curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh`, skipped if `/usr/local/bin/uv` exists
  6. Take the repo URL as `$1` (required; print usage and exit 2 without it). Write it to `/etc/readlater/repo-url` and run `sudo -u readlater git clone "$1" /srv/readlater/app`, skipped if `/srv/readlater/app/.git` exists
  7. Copy `deploy/systemd/*.service` to `/etc/systemd/system/` and run `systemctl daemon-reload`, unless `READLATER_NO_SYSTEMD=1`
  8. `install -m 755 deploy/update.sh /usr/local/sbin/readlater-update`, read from the script's own directory, so updates are one command
  9. Print the next steps: copy the config, write the token, run `sudo readlater-update`

  It never overwrites `/etc/readlater/config.toml` or `telegram.secret`, and never touches `/srv/readlater/data`.
- `update.sh [ref]`, run as root (ref defaults to `main`). Steps:
  1. `sudo -u readlater git -C /srv/readlater/app fetch "$(cat /etc/readlater/repo-url)" "$ref"`, then `git checkout --detach FETCH_HEAD`. If the checkout has local changes, refuse and exit 1 (`git diff --quiet`)
  2. `sudo -u readlater /usr/local/bin/uv sync --frozen --no-dev --project /srv/readlater/app`
  3. `sudo -u readlater /srv/readlater/app/.venv/bin/readlater init-db`
  4. Unless `READLATER_NO_SYSTEMD=1`: `systemctl enable readlater-bot readlater-worker`, then `systemctl restart readlater-bot readlater-worker`
  5. Re-install itself from the new checkout with `install -m 755 /srv/readlater/app/deploy/update.sh /usr/local/sbin/readlater-update`, so script fixes ship with the code
  6. Print the deployed commit with `git -C /srv/readlater/app log -1 --oneline`

  With `set -e`, a failing `uv sync` stops the script before the restart.
- `deploy/README.md` is the runbook: the Task 5 steps in order, "ship an update" (`git push origin main && ssh <node-a> sudo readlater-update`), the GitHub safety checklist (Task 5, step 0), viewing logs (`journalctl -u readlater-worker -f`), and running `readlater` commands as the service user (`sudo -u readlater READLATER_CONFIG=/etc/readlater/config.toml /srv/readlater/app/.venv/bin/readlater …`).

- [ ] **Step 1: Write the failing test** (in `tests/test_deploy_files.py`)

```python
def test_scripts_use_the_unit_paths():
    install, update = Path("deploy/install.sh").read_text(), Path("deploy/update.sh").read_text()
    for s in (install, update):
        assert s.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert "/etc/readlater/repo-url" in install and "/etc/readlater/repo-url" in update
    assert "/srv/readlater/data" in install
    assert "uv sync --frozen --no-dev" in update
    assert update.index("uv sync") < update.index("systemctl restart")   # broken deps never restart
    assert "/usr/local/sbin/readlater-update" in install and "/usr/local/sbin/readlater-update" in update
    for s in (install, update):   # never overwrite the user's config or token, never delete anything
        assert not re.search(r">\s*/etc/readlater/(config\.toml|telegram\.secret)", s) and "rm " not in s
```

- [ ] **Step 2:** Run `uv run pytest tests/test_deploy_files.py -q`. Expected: FAIL (the scripts don't exist).
- [ ] **Step 3:** Write `install.sh`, `update.sh` and `README.md`, then `chmod +x deploy/*.sh`.
- [ ] **Step 4:** Run `uv run pytest -q` and `bash -n deploy/install.sh deploy/update.sh`. Expected: all passed, no syntax errors.
- [ ] **Step 5: Container check** of a fresh install, a push, an update, a re-run, and data surviving the re-run:

```bash
docker run --rm -v "$PWD":/src:ro -e READLATER_NO_SYSTEMD=1 debian:trixie bash -euxc '
  apt-get update -qq && apt-get install -y -qq sudo git >/dev/null
  git config --system --add safe.directory "*"
  git clone -q /src /tmp/origin                       # stands in for the GitHub repo
  bash /tmp/origin/deploy/install.sh /tmp/origin
  readlater-update
  sudo -u readlater touch /srv/readlater/data/marker
  git -C /tmp/origin -c user.email=t@t -c user.name=t commit -q --allow-empty -m "new release"
  bash /tmp/origin/deploy/install.sh /tmp/origin && readlater-update
  test "$(git -C /srv/readlater/app rev-parse HEAD)" = "$(git -C /tmp/origin rev-parse HEAD)"
  test -f /srv/readlater/data/marker && test -f /srv/readlater/data/readlater.db
  sudo -u readlater /srv/readlater/app/.venv/bin/readlater --help >/dev/null && echo CONTAINER-OK'
```

  Expected: the last line is `CONTAINER-OK`: the update deployed the new commit, and re-running both scripts kept the data. This is the "container: rerun keeps data" check.
- [ ] **Step 6: Commit** with message `feat: install and update scripts with runbook`.

---

### Task 5: Go live (manual, on the machines)

Follow `deploy/README.md`. Each step lists the result that means it worked.

- [ ] **Step 0: GitHub safety, before the first push.**
  - Turn on 2FA for your GitHub account. Anyone with the account could ship code to the server at the next update.
  - Create the public repo, then under Settings:
    - **Branches:** a rule for `main` that blocks force-pushes and deletion.
    - **Code security:** turn on secret scanning and push protection.
  - On the dev machine: `git remote add origin git@github.com:<owner>/<repo>.git && git push -u origin main`.

  Expected: the repo shows the code, and `git log --format=%ae` contains only your no-reply address.
- [ ] **Step 1: Node B (Ollama).**
  - Confirm `ss -ltnp | grep 11434` shows `0.0.0.0:11434`. The `host.conf` drop-in persists it across reboots: check with `sudo reboot`, then `curl http://<ollama-host>:11434/api/tags` from node A.
  - Confirm `qwen3.5:4b` is listed. Expected: JSON that includes `qwen3.5:4b`.
- [ ] **Step 2: Node A basics.**
  - `sudo apt install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades`
  - `sudo timedatectl set-timezone <Region/City>`
  - Confirm `tailscale status` lists node B.

  Expected: `curl http://<ollama-host>:11434/api/tags` works from node A.
- [ ] **Step 3: Install.** On node A: `git clone https://github.com/<owner>/<repo>.git /tmp/readlater`, read `deploy/install.sh`, then `sudo bash /tmp/readlater/deploy/install.sh https://github.com/<owner>/<repo>.git`. Expected: the "next steps" text, `id readlater` works, and `/usr/local/sbin/readlater-update` exists.
- [ ] **Step 4: Deploy the code.** On node A: `sudo readlater-update`. Expected: `deployed: <commit>`, the same commit as `git log -1 --oneline` on the dev machine.
- [ ] **Step 5: Config and token.**
  - `sudo cp /srv/readlater/app/deploy/config.server.toml /etc/readlater/config.toml`, then set `[ollama] url` to node B's name
  - `sudo install -m 600 /dev/null /etc/readlater/telegram.secret && sudo nano /etc/readlater/telegram.secret`, pasting the BotFather token.
  - Run `check`: `sudo -u readlater env READLATER_CONFIG=/etc/readlater/config.toml READLATER_TELEGRAM_TOKEN="$(sudo cat /etc/readlater/telegram.secret)" /srv/readlater/app/.venv/bin/readlater check`.

  Expected: everything is `ok` except `allowed users`.
- [ ] **Step 6: Allow yourself and start.**
  - `sudo systemctl start readlater-bot readlater-worker`. Send the bot a message from your phone, then read your ID from `journalctl -u readlater-bot | grep "ignoring message from user id"`.
  - Put the ID in `/etc/readlater/config.toml` and run `sudo systemctl restart readlater-bot readlater-worker`.
  - Re-run `check`. Expected: all `ok`.
  - Also test with node B down: run `sudo systemctl stop ollama` on node B and share a link. The worker log shows `deferred: ollama unreachable`, and `systemctl status readlater-worker` stays `active`. Start Ollama again; the summary arrives within about a minute plus the summary time.
- [ ] **Step 7: Phone test.** Share 3 links. Expected: "Saved" right away, formatted summaries within minutes, and "Already saved" when you share one again.
- [ ] **Step 8: Power test.** Unplug node A for 10 seconds and plug it back in. Expected, without logging in: `systemctl is-active readlater-bot readlater-worker` prints `active` twice, and a link shared during the outage gets its summary afterwards.
- [ ] **Step 9 (optional): Email backlog.**
  - `scp export.mbox <node-a>:/tmp/`
  - Dry run as the service user: `sudo -u readlater env READLATER_CONFIG=/etc/readlater/config.toml /srv/readlater/app/.venv/bin/readlater import-mail --mbox /tmp/export.mbox --dry-run`. Then run it without `--dry-run`, and delete the mbox afterwards.

  Expected: the counts match your dev dry run, and a second run says `0 queued`.
- [ ] **Step 10:** Stop using `readlater bot` on the dev machine from now on (one bot per token). Commit `deploy/README.md` with any fixes you found on the way, with message `docs: deployment runbook fixes`.

---

## Self-review notes

- **Spec coverage:**
  - The public GitHub repo (read-only on the server, 2FA, branch protection, secret scanning, no machine-specific values committed).
  - Debian on both nodes, Tailscale, Ollama on node B bound for the network, the app user and repo layout, uv, systemd units with hardening and `LoadCredential`, unattended-upgrades, the single-box fallback and the "pull the power cord" check are all covered.
  - Backups and LUKS are dropped per the user and assumption 3. The web unit waits for the UI plan.
  - The spec's `nftables` rules were dropped earlier by the user (trusted LAN).
- **Timing:** measured on node B, a 2-part essay took 12 minutes, so the core plan's 12-part maximum is about 50 minutes, within the user's one-hour budget.
- **The one code change Review Focus 1 needs** (the schema created on first use) is Task 1. It also closes a deferred minor from the core review.
