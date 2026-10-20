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
    assert "uv sync --frozen --no-dev" in update
    assert update.index("uv sync") < update.index("systemctl restart")   # broken deps never restart
    assert "/usr/local/sbin/readlater-update" in install and "/usr/local/sbin/readlater-update" in update
    for s in (install, update):   # never overwrite the user's config or token, never delete anything
        assert not re.search(r">\s*/etc/readlater/(config\.toml|telegram\.secret)", s) and "rm " not in s
