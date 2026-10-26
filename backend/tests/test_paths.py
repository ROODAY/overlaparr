from pathlib import Path

from overlaparr import paths


def test_empty_config_dir_env_falls_back(monkeypatch):
    monkeypatch.setenv("OVERLAPARR_CONFIG_DIR", "")
    assert paths.config_dir() == Path("/config")


def test_config_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OVERLAPARR_CONFIG_DIR", str(tmp_path))
    assert paths.config_file() == tmp_path / "config.yaml"
