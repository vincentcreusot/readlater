from pathlib import Path

import httpx

from readlater.check import CheckResult, check_config, check_ollama, check_telegram
from readlater.cli import main
from readlater.config import PathsCfg, load_config


def CFG(tmp):
    return load_config(Path("config.example.toml")).model_copy(update={"paths": PathsCfg(data=tmp)})


def tags(*names):
    return httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"models": [{"name": n} for n in names]})))


def test_placeholder_user_id_fails(tmp_path):
    r = {c.name: c for c in check_config(CFG(tmp_path))}
    assert not r["allowed users"].ok and "allowed_user_ids" in r["allowed users"].detail
    assert r["data dir"].ok


def test_unwritable_data_dir_fails(tmp_path):
    ro = tmp_path / "ro"; ro.mkdir(); ro.chmod(0o500)
    try:
        r = {c.name: c for c in check_config(CFG(ro / "data"))}
        assert not r["data dir"].ok
    finally:
        ro.chmod(0o700)


def test_ollama_ok_and_missing_model(tmp_path):
    assert check_ollama(CFG(tmp_path), tags("qwen3.5:4b", "nomic-embed-text:latest")).ok
    bad = check_ollama(CFG(tmp_path), tags("llama3:8b"))
    assert not bad.ok and bad.detail == "model qwen3.5:4b not pulled; run: ollama pull qwen3.5:4b"


def test_ollama_unreachable(tmp_path):
    def h(r):
        raise httpx.ConnectError("Connection refused")
    res = check_ollama(CFG(tmp_path), httpx.Client(transport=httpx.MockTransport(h)))
    assert not res.ok and res.detail.startswith(f"cannot reach {CFG(tmp_path).ollama.url}")


def test_telegram_ok_and_bad_token():
    ok = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok": True, "result": {"username": "my_reader_bot"}})))
    assert check_telegram("1:T", ok) == CheckResult("telegram", True, "@my_reader_bot")
    bad = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"})))
    res = check_telegram("1:SECRET", bad)
    assert not res.ok and "Unauthorized" in res.detail and "SECRET" not in res.detail


def test_telegram_missing_token():
    assert not check_telegram(None, httpx.Client()).ok


def test_cli_check_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("readlater.check.run_checks", lambda cfg, client, **kw: [CheckResult("x", False, "bad")])
    cfg = Path("config.example.toml").read_text().replace('data = "./data"', f'data = "{tmp_path}"')
    (tmp_path / "c.toml").write_text(cfg); monkeypatch.setenv("READLATER_CONFIG", str(tmp_path / "c.toml"))
    assert main(["check"]) == 1 and "FAIL x: bad" in capsys.readouterr().out


def test_unreplaced_placeholder_is_named(tmp_path):
    cfg = CFG(tmp_path)
    cfg.ollama.url = "http://<ollama-host>:11434"
    res = check_ollama(cfg, httpx.Client())
    assert not res.ok and res.detail == "replace the <ollama-host> placeholder in [ollama] url"


def test_check_does_not_create_the_data_dir(tmp_path):
    target = tmp_path / "not-yet"
    r = {c.name: c for c in check_config(CFG(target))}
    assert r["data dir"].ok and "will be created" in r["data dir"].detail and not target.exists()
