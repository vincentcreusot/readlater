"""readlater check: confirm the config, data folder, Ollama and the Telegram token before going live."""
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from readlater.config import Config
from readlater.sources.telegram_bot import TelegramAPI, TelegramError, load_token

PLACEHOLDER_USER_ID = 123456789


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _first_line(e: Exception) -> str:
    return (str(e).splitlines() or [type(e).__name__])[0]


def check_config(cfg: Config) -> list[CheckResult]:
    ids = cfg.telegram.allowed_user_ids
    if not ids or PLACEHOLDER_USER_ID in ids:
        users = CheckResult("allowed users", False, "set [telegram] allowed_user_ids to your numeric Telegram ID")
    else:
        users = CheckResult("allowed users", True, ", ".join(map(str, ids)))
    return [users, _check_data_dir(cfg.paths.data)]


def _check_data_dir(path) -> CheckResult:
    """Whether readlater can write there, without creating anything."""
    if path.exists():
        try:
            fd, probe = tempfile.mkstemp(dir=path)
            os.close(fd)
            os.unlink(probe)
            return CheckResult("data dir", True, str(path))
        except OSError as e:
            return CheckResult("data dir", False, f"cannot write to {path}: {_first_line(e)}")
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    if os.access(parent, os.W_OK | os.X_OK):
        return CheckResult("data dir", True, f"{path} (will be created)")
    return CheckResult("data dir", False, f"cannot create {path}: {parent} is not writable")


def check_ollama(cfg: Config, client: httpx.Client) -> CheckResult:
    url, model = cfg.ollama.url, cfg.ollama.model
    placeholder = re.search(r"<[^>]+>", url)
    if placeholder:
        return CheckResult("ollama", False, f"replace the {placeholder.group(0)} placeholder in [ollama] url")
    try:
        r = client.get(f"{url}/api/tags", timeout=10)
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
    except Exception as e:   # unreachable host, bad URL placeholder, non-JSON answer
        return CheckResult("ollama", False, f"cannot reach {url}: {_first_line(e)}")
    if model not in names and f"{model}:latest" not in names:
        return CheckResult("ollama", False, f"model {model} not pulled; run: ollama pull {model}")
    return CheckResult("ollama", True, f"{model} on {url}")


def check_telegram(token: str | None, client: httpx.Client) -> CheckResult:
    if not token:
        return CheckResult("telegram", False,
                           "no token: set READLATER_TELEGRAM_TOKEN or use systemd LoadCredential=telegram")
    try:
        me = TelegramAPI(token, client).call("getMe")
    except TelegramError as e:
        return CheckResult("telegram", False, str(e))
    return CheckResult("telegram", True, f"@{me.get('username', '?')}")


def run_checks(cfg: Config, client: httpx.Client,
               token_loader: Callable[[], str] = load_token) -> list[CheckResult]:
    try:
        token = token_loader()
    except RuntimeError:
        token = None
    return [*check_config(cfg), check_ollama(cfg, client), check_telegram(token, client)]
