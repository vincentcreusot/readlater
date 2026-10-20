#!/usr/bin/env bash
set -euo pipefail
# One-time setup of readlater on a Debian 13 server. Safe to run again: it never touches
# /srv/readlater/data, /etc/readlater/config.toml or /etc/readlater/telegram.secret.
#
#   sudo bash deploy/install.sh https://github.com/<owner>/<repo>.git

REPO_URL="${1:-}"
if [ -z "$REPO_URL" ]; then
  echo "usage: $0 <git repo URL>" >&2
  exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
HERE="$(cd "$(dirname "$0")" && pwd)"

apt-get update -qq
apt-get install -y -qq git sqlite3 curl ca-certificates sudo >/dev/null

id readlater >/dev/null 2>&1 ||
  useradd --system --create-home --home-dir /srv/readlater --shell /usr/sbin/nologin readlater
install -d -o readlater -g readlater /srv/readlater/data
install -d -m 755 /etc/readlater

if [ ! -x /usr/local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

printf '%s\n' "$REPO_URL" > /etc/readlater/repo-url
[ -d /srv/readlater/app/.git ] || sudo -u readlater -H git clone -q "$REPO_URL" /srv/readlater/app

if [ "${READLATER_NO_SYSTEMD:-0}" != 1 ]; then
  install -m 644 "$HERE"/systemd/readlater-*.service /etc/systemd/system/
  systemctl daemon-reload
fi
install -m 755 "$HERE/update.sh" /usr/local/sbin/readlater-update

cat <<'NEXT'
readlater is installed. Next:
  1. sudo cp /srv/readlater/app/deploy/config.server.toml /etc/readlater/config.toml
     and set [ollama] url (and later allowed_user_ids) in it
  2. sudo install -m 600 /dev/null /etc/readlater/telegram.secret
     then paste the BotFather token into it (sudo nano /etc/readlater/telegram.secret)
  3. sudo readlater-update
NEXT
