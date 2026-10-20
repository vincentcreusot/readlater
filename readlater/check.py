"""readlater check: confirm the config, data folder, Ollama and the Telegram token before going live."""
import os
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
    try:
        cfg.paths.data.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=cfg.paths.data)
        os.close(fd)
        os.unlink(probe)
        data = CheckResult("data dir", True, str(cfg.paths.data))
    except OSError as e:
        data = CheckResult("data dir", False, f"cannot write to {cfg.paths.data}: {_first_line(e)}")
    return [users, data]


def check_ollama(cfg: Config, client: httpx.Client) -> CheckResult:
    url, model = cfg.ollama.url, cfg.ollama.model
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
