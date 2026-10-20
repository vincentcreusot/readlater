#!/usr/bin/env bash
set -euo pipefail
# Deploy the latest code from the repo recorded at install time, then restart the services.
# A failed dependency sync stops here, so the running services keep the old code.
#
#   sudo readlater-update [ref]      (ref defaults to main)

REF="${1:-main}"
if [ "$(id -u)" -ne 0 ]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
APP=/srv/readlater/app
CONFIG=/etc/readlater/config.toml
URL="$(cat /etc/readlater/repo-url)"
as_app() { sudo -u readlater -H "$@"; }

if ! as_app git -C "$APP" diff --quiet || ! as_app git -C "$APP" diff --cached --quiet; then
  echo "local changes in $APP; refusing to update (inspect with: sudo -u readlater git -C $APP status)" >&2
  exit 1
fi
as_app git -C "$APP" fetch -q "$URL" "$REF"
as_app git -C "$APP" checkout -q --detach FETCH_HEAD
as_app /usr/local/bin/uv sync --frozen --no-dev --quiet --project "$APP"

if [ ! -f "$CONFIG" ]; then
  echo "code updated, but $CONFIG is missing: copy $APP/deploy/config.server.toml there, then run this again"
elif [ "${READLATER_NO_SYSTEMD:-0}" = 1 ]; then
  as_app env READLATER_CONFIG="$CONFIG" "$APP/.venv/bin/readlater" init-db >/dev/null
else
  as_app env READLATER_CONFIG="$CONFIG" "$APP/.venv/bin/readlater" init-db >/dev/null
  units=(readlater-worker)
  if [ -s /etc/readlater/telegram.secret ]; then
    units+=(readlater-bot)
  else
    echo "no bot token in /etc/readlater/telegram.secret yet: starting the worker only"
  fi
  systemctl enable -q "${units[@]}"
  systemctl restart "${units[@]}"
fi

install -m 755 "$APP/deploy/update.sh" /usr/local/sbin/readlater-update   # script fixes ship with the code
echo "deployed: $(as_app git -C "$APP" log -1 --oneline)"
