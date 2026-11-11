import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own sessions folder, database and settings."""
    monkeypatch.setenv("AKU_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AKU_DB", str(tmp_path / "aku.db"))
    monkeypatch.setenv("AKU_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    from aku import config

    config.save_settings({"notifications": False, "output_folder": str(tmp_path / "out"), "user_name": "Taylor"})
    return tmp_path
