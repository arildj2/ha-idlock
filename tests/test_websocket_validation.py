"""Tests for WebSocket boundary validation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.ha_idlock.websocket import (
    _validate_setting_value,
    _validate_slot,
)


def test_slot_uses_discovered_hardware_limit() -> None:
    """A stale stored maximum cannot authorize an unsupported hardware slot."""
    connection = SimpleNamespace(send_error=Mock())
    lock = SimpleNamespace(max_slots=255)

    assert _validate_slot(connection, {"id": 1, "slot": 26}, lock, 25) is None
    connection.send_error.assert_called_once_with(
        1, "invalid_slot", "Slot must be 1-25"
    )


@pytest.mark.parametrize("value", ["true", 1, 0, None])
def test_boolean_setting_rejects_truthy_coercion(value: object) -> None:
    """Only actual JSON booleans are accepted for boolean settings."""
    connection = SimpleNamespace(send_error=Mock())
    msg = {"id": 1, "setting": "rfid_enabled", "value": value}
    assert _validate_setting_value(connection, msg) is False


def test_integer_setting_rejects_bool_and_out_of_range() -> None:
    """Python bool is not accepted as an integer setting value."""
    for value in (True, -1, 3):
        connection = SimpleNamespace(send_error=Mock())
        msg = {"id": 1, "setting": "audio_volume", "value": value}
        assert _validate_setting_value(connection, msg) is False
