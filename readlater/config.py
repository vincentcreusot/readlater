"""Settings from config.toml (path given, or $READLATER_CONFIG)."""
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TelegramCfg(_Section):
    allowed_user_ids: list[int] = []


class MailCfg(_Section):
    host: str = ""
    user: str = ""
    folder: str = "INBOX"
    own_addresses: list[str] = []


class OllamaCfg(_Section):
    url: str
    model: str = "qwen3.5:4b"
    embed_model: str = "nomic-embed-text"
    num_ctx: int = 16384


class FetchCfg(_Section):
    contact: str = ""   # added to the User-Agent; some sites (Wikipedia) refuse bots without one


class PathsCfg(_Section):
    data: Path


class Config(_Section):
    telegram: TelegramCfg = TelegramCfg()
    mail: MailCfg = MailCfg()
    fetch: FetchCfg = FetchCfg()
    ollama: OllamaCfg
    paths: PathsCfg

    @property
    def db_path(self) -> Path:
        return self.paths.data / "readlater.db"


def load_config(path: Path | None = None) -> Config:
    if path is None:
        env = os.environ.get("READLATER_CONFIG")
        if not env:
            raise FileNotFoundError("no config path given and READLATER_CONFIG is not set")
        path = Path(env)
    with open(path, "rb") as f:
        return Config.model_validate(tomllib.load(f))
