import configparser
import re
import tomllib
from pathlib import Path

from readlater.cli import build_parser

UNITS = {"bot": Path("deploy/systemd/readlater-bot.service"),
         "worker": Path("deploy/systemd/readlater-worker.service")}


def unit(path):
    p = configparser.ConfigParser(strict=False, interpolation=None)
    p.optionxform = str
    p.read(path)
    return p


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


def test_scripts_use_the_unit_paths():
    install, update = Path("deploy/install.sh").read_text(), Path("deploy/update.sh").read_text()
    for s in (install, update):
        assert s.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert "/etc/readlater/repo-url" in install and "/etc/readlater/repo-url" in update
    assert "/srv/readlater/data" in install
    assert "uv sync --locked --no-dev" in update      # --frozen would ignore a stale uv.lock
    assert update.index("uv sync") < update.index("systemctl restart")   # broken deps never restart
    assert "/usr/local/sbin/readlater-update" in install and "/usr/local/sbin/readlater-update" in update
    for s in (install, update):   # never overwrite the user's config or token, never delete anything
        assert not re.search(r">\s*/etc/readlater/(config\.toml|telegram\.secret)", s) and "rm " not in s


def test_failed_sync_rolls_back_the_checkout():
    update = Path("deploy/update.sh").read_text()
    assert 'prev="$(' in update and 'checkout -q --detach "$prev"' in update
    assert update.index('prev="$(') < update.index("fetch")


def test_update_refreshes_unit_files_before_restart():
    update = Path("deploy/update.sh").read_text()
    assert '"$APP"/deploy/systemd/readlater-*.service /etc/systemd/system/' in update
    assert update.index("daemon-reload") < update.index("systemctl restart")


def test_uv_is_pinned_and_checksummed():
    install = Path("deploy/install.sh").read_text()
    assert re.search(r'^UV_VERSION=0\.\d+\.\d+\b', install, re.M)
    assert "sha256sum -c" in install and not re.search(r"\|\s*(sudo\s+)?(env(\s+\S+=\S*)*\s+)?(ba)?sh\b", install)
    for arch in ("x86_64", "aarch64"):
        assert re.search(arch + r"\)\s+UV_SHA256=[0-9a-f]{64}", install)


def test_update_refuses_untracked_files_and_concurrent_runs():
    update = Path("deploy/update.sh").read_text()
    assert 'status --porcelain' in update and "diff --quiet" not in update
    assert "flock -n" in update and update.index("flock -n") < update.index("fetch")


def test_python_version_is_pinned_for_the_server():
    assert Path(".python-version").read_text().strip() == "3.13"


def test_runbook_never_puts_the_token_on_a_command_line():
    readme = Path("deploy/README.md").read_text()
    assert "READLATER_TELEGRAM_TOKEN=" not in readme and "cat /etc/readlater/telegram.secret" not in readme
    assert "LoadCredential=telegram:/etc/readlater/telegram.secret" in readme
