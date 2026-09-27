import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("PLC_HOST", "10.0.0.5")
    monkeypatch.setenv("PLC_UNIT_ID", "3")
    monkeypatch.setenv("API_KEY", "super-secret")
    s = Settings(_env_file=None)
    assert s.plc_host == "10.0.0.5"
    assert s.plc_port == 502
    assert s.plc_unit_id == 3
    assert "super-secret" not in repr(s)  # secrets masked
    assert s.api_key.get_secret_value() == "super-secret"


def test_missing_host_fails(monkeypatch):
    monkeypatch.delenv("PLC_HOST", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_port_fails(monkeypatch):
    monkeypatch.setenv("PLC_HOST", "10.0.0.5")
    monkeypatch.setenv("PLC_PORT", "70000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
