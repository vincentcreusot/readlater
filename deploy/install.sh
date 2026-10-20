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
UV_VERSION=0.13.3   # bump together with both checksums from the release's *.sha256 files

apt-get update -qq
apt-get install -y -qq git sqlite3 curl ca-certificates sudo >/dev/null

id readlater >/dev/null 2>&1 ||
  useradd --system --create-home --home-dir /srv/readlater --shell /usr/sbin/nologin readlater
install -d -o readlater -g readlater /srv/readlater/data
install -d -m 755 /etc/readlater

# uv: a pinned release, verified against its published checksum before it is installed
if [ "$(/usr/local/bin/uv --version 2>/dev/null | cut -d' ' -f2)" != "$UV_VERSION" ]; then
  case "$(uname -m)" in
    x86_64)  UV_SHA256=e4f169ec380f1793b898a6f1fcbacd22db25cf7ca0349a6e96ffb85b69998d04 ;;
    aarch64) UV_SHA256=611ff684f193a84d2fdad29c5f12505d02ece94984c7c982cdde950eba5c95b9 ;;
    *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac
  target="uv-$(uname -m)-unknown-linux-gnu"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/uv.tar.gz" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$target.tar.gz"
  echo "$UV_SHA256  $tmp/uv.tar.gz" | sha256sum -c --quiet
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  install -m 755 "$tmp/$target/uv" /usr/local/bin/uv
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
