from pathlib import Path

from readlater.cli import main


def test_add_and_readd(tmp_path, monkeypatch, capsys):
    cfg = Path("config.example.toml").read_text().replace('data = "./data"', f'data = "{tmp_path}"')
    (tmp_path / "c.toml").write_text(cfg)
    monkeypatch.setenv("READLATER_CONFIG", str(tmp_path / "c.toml"))
    assert main(["init-db"]) == 0
    assert main(["add", "https://example.com/a"]) == 0
    assert "queued article 1" in capsys.readouterr().out
    main(["add", "https://example.com/a"])
    assert "already saved as article 1" in capsys.readouterr().out
