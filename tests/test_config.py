from pathlib import Path

from readlater.config import load_config


def test_load_config_reads_example():
    cfg = load_config(Path("config.example.toml"))
    assert cfg.telegram.allowed_user_ids == [123456789]
    assert cfg.ollama.model == "qwen3.5:4b" and cfg.ollama.num_ctx == 16384
    assert cfg.mail.own_addresses == ["you@example.com"]
    assert cfg.db_path == Path("./data/readlater.db")


def test_load_config_from_env(monkeypatch):
    monkeypatch.setenv("READLATER_CONFIG", "config.example.toml")
    assert load_config().ollama.url == "http://192.168.1.21:11434"


def test_unknown_section_is_ignored(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(Path("config.example.toml").read_text() + "\n[dedupe]\nnear_duplicate_cosine = 0.95\n")
    load_config(p)
