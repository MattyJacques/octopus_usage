import pytest

from octopus_usage.config import ConfigError, load_config


def test_load_config_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OCTOPUS_API_KEY", raising=False)
    monkeypatch.delenv("OCTOPUS_ACCOUNT_NUMBER", raising=False)
    env = tmp_path / ".env"
    env.write_text("OCTOPUS_API_KEY=sk_live_abc\nOCTOPUS_ACCOUNT_NUMBER=A-12345678\n")
    cfg = load_config(str(env))
    assert cfg.api_key == "sk_live_abc"
    assert cfg.account_number == "A-12345678"
    assert cfg.db_path == "octopus_usage.db"


def test_load_config_missing_credentials_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("OCTOPUS_API_KEY", raising=False)
    monkeypatch.delenv("OCTOPUS_ACCOUNT_NUMBER", raising=False)
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "missing.env"))
