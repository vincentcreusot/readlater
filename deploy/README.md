# Deploying readlater

The bot and worker run on one always-on Debian 13 server ("node A") as two systemd services. Summaries come from Ollama on an inference machine ("node B"), reached over Tailscale. On a single box, point the config at `http://localhost:11434`.

Placeholders below:
- `<node-a>`: the server.
- `<ollama-host>`: node B's Tailscale name.
- `<owner>/<repo>`: the GitHub repo.

## Before the first deploy: GitHub safety

The server pulls this public repo over HTTPS with no credentials and can't push. Your GitHub account is what protects the server, so:

- **2FA:** turn on two-factor authentication for the account.
- **Protect `main`:** under Settings → Rules, add a rule for the default branch that blocks force-pushes and deletion.
- **Catch secrets:** under Settings → Code security, turn on secret scanning and push protection.
- **Nothing machine-specific in commits:** no hostnames, tokens, Telegram IDs or real config. Those live only in `/etc/readlater/` on the server.

## Node B (Ollama)

Ollama must listen on the network, not only on localhost:

```bash
sudo tee /etc/systemd/system/ollama.service.d/host.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
sudo systemctl daemon-reload && sudo systemctl restart ollama
ollama pull qwen3.5:4b
```

From node A, `curl http://<ollama-host>:11434/api/tags` should list `qwen3.5:4b`.

## Node A: first install

```bash
# basics
sudo apt install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades
sudo timedatectl set-timezone <Region/City>      # "Saved on" dates use local time
tailscale status                                  # node B must be listed

# install (read deploy/install.sh first; it is short)
git clone https://github.com/<owner>/<repo>.git /tmp/readlater
sudo bash /tmp/readlater/deploy/install.sh https://github.com/<owner>/<repo>.git

# config and bot token
sudo cp /srv/readlater/app/deploy/config.server.toml /etc/readlater/config.toml
sudo nano /etc/readlater/config.toml              # set [ollama] url
sudo install -m 600 /dev/null /etc/readlater/telegram.secret
sudo nano /etc/readlater/telegram.secret          # paste the BotFather token

# deploy and start
sudo readlater-update
```

### Allow your Telegram account

1. Send the bot any message from your phone.
2. Run `journalctl -u readlater-bot | grep "ignoring message from user id"` and note the number.
3. Put the number in `allowed_user_ids` in `/etc/readlater/config.toml`.
4. Run `sudo systemctl restart readlater-bot readlater-worker`.

### Check everything

```bash
sudo -u readlater env READLATER_CONFIG=/etc/readlater/config.toml \
  READLATER_TELEGRAM_TOKEN="$(sudo cat /etc/readlater/telegram.secret)" \
  /srv/readlater/app/.venv/bin/readlater check
```

Every line should say `ok`. A `FAIL` line names the problem, e.g. `model qwen3.5:4b not pulled; run: ollama pull qwen3.5:4b`.

## Shipping an update

On the dev machine:

```bash
git push origin main
ssh <node-a> sudo readlater-update     # or: sudo readlater-update <tag-or-branch>
```

`readlater-update` fetches the commit, installs the locked dependencies (`uv sync --locked`), refreshes the systemd units and restarts both services. If the dependencies fail to install (for example, `pyproject.toml` changed but `uv.lock` wasn't updated: run `uv lock` and commit it), it checks the previous commit back out and stops without restarting, so the old code keeps running. It refuses to run if someone edited files in `/srv/readlater/app` by hand.

To roll back to an earlier release: `sudo readlater-update <commit-sha>`.

Run exactly one bot per token: once the server bot runs, stop running `readlater bot` on the dev machine.

## Day to day

| What | Command |
|---|---|
| Follow the worker | `journalctl -u readlater-worker -f` |
| Follow the bot | `journalctl -u readlater-bot -f` |
| Service status | `systemctl status readlater-bot readlater-worker` |
| Any readlater command | `sudo -u readlater env READLATER_CONFIG=/etc/readlater/config.toml /srv/readlater/app/.venv/bin/readlater <command>` |
| Re-queue failures | same, with `retry` (or send `/retry` to the bot) |
| Export to Markdown | same, with `export /srv/readlater/data/export` |

## One-time email import

```bash
scp export.mbox <node-a>:/tmp/
# on node A, as the service user (see "Any readlater command" above):
...readlater import-mail --mbox /tmp/export.mbox --dry-run   # check the list
...readlater import-mail --mbox /tmp/export.mbox             # queue them; a second run says 0 queued
```

Delete the mbox when it's done. Imported articles queue behind anything you share from your phone.

## Checks that it survives

- **Node B down:** with Ollama stopped, a shared link waits in the queue (`deferred: ollama unreachable` in the worker log). The summary arrives once Ollama is back.
- **Power loss:** unplug node A and plug it back in. Without logging in, `systemctl is-active readlater-bot readlater-worker` prints `active` twice, and links shared meanwhile get summaries. Telegram keeps undelivered messages for 24 hours.
