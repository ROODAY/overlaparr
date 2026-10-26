import os
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("OVERLAPARR_CONFIG_DIR") or "/config")


def config_file() -> Path:
    return config_dir() / "config.yaml"


def secrets_file() -> Path:
    return config_dir() / "secrets.yaml"


def db_file() -> Path:
    return config_dir() / "overlaparr.db"


def log_file() -> Path:
    return config_dir() / "logs" / "overlaparr.log"
