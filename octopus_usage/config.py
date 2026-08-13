"""Configuration loaded from environment / .env file."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass
class Config:
    api_key: str
    account_number: str
    db_path: str = "octopus_usage.db"


def load_config(env_file: str | None = ".env") -> Config:
    if env_file and Path(env_file).exists():
        load_dotenv(env_file, override=True)
    api_key = os.environ.get("OCTOPUS_API_KEY", "").strip()
    account = os.environ.get("OCTOPUS_ACCOUNT_NUMBER", "").strip()
    if not api_key or not account:
        raise ConfigError(
            "Set OCTOPUS_API_KEY and OCTOPUS_ACCOUNT_NUMBER in a .env file "
            "(see .env.example; values are under Personal details -> API access "
            "in your Octopus dashboard)."
        )
    return Config(
        api_key=api_key,
        account_number=account,
        db_path=os.environ.get("OCTOPUS_DB_PATH", "octopus_usage.db"),
    )
