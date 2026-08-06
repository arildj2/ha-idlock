"""Tests for serialized and verified Zigbee operations."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.ha_idlock.lock_device import IDLockDevice


def _device(cluster: object) -> IDLockDevice:
    device = IDLockDevice(None, "00:11:22:33:44:55:66:77")
    device._cluster = cluster  # noqa: SLF001
    return device


async def test_set_pin_requires_status_and_matching_readback() -> None:
    """The store may only be updated after command and read-back success."""
    cluster = SimpleNamespace(
        set_pin_code=AsyncMock(return_value=SimpleNamespace(status=0)),
        get_pin_code=AsyncMock(
            return_value=SimpleNamespace(user_status=1, code="1234")
        ),
    )
    device = _device(cluster)

    assert await device.async_set_pin(1, "1234") is True

    cluster.get_pin_code.return_value = SimpleNamespace(user_status=1, code="9999")
    assert await device.async_set_pin(1, "1234") is False

    cluster.set_pin_code.return_value = SimpleNamespace(status=1)
    cluster.get_pin_code.reset_mock()
    assert await device.async_set_pin(1, "1234") is False
    cluster.get_pin_code.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "command", "read_method", "readback"),
    [
        ("async_clear_pin", "clear_pin_code", "get_pin_code", SimpleNamespace(user_status=0, code=None)),
        ("async_clear_rfid", "clear_rfid_code", "get_rfid_code", SimpleNamespace(user_status=0)),
    ],
)
async def test_clear_operations_verify_empty_slot(
    method: str, command: str, read_method: str, readback: object
) -> None:
    """Clear commands require a successful empty-slot read-back."""
    cluster = SimpleNamespace(
        **{
            command: AsyncMock(return_value=SimpleNamespace(status=0)),
            read_method: AsyncMock(return_value=readback),
        }
    )
    assert await getattr(_device(cluster), method)(2) is True


async def test_operations_are_serialized_per_device() -> None:
    """Concurrent clients never send overlapping requests to one lock."""
    active = 0
    maximum_active = 0

    async def get_pin_code(_slot: int) -> object:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(user_status=1, code="1234")

    device = _device(SimpleNamespace(get_pin_code=get_pin_code))
    await asyncio.gather(*(device.async_get_pin(slot) for slot in range(1, 4)))
    assert maximum_active == 1


async def test_device_info_uses_one_overall_timeout() -> None:
    """A settings refresh cannot accumulate a timeout per attribute group."""
    async def slow_capability_read() -> None:
        await asyncio.sleep(1)

    device = _device(SimpleNamespace())
    device._read_firmware_versions = AsyncMock()  # noqa: SLF001
    device._read_capabilities = AsyncMock(side_effect=slow_capability_read)  # noqa: SLF001
    device._read_idlock_attributes = AsyncMock()  # noqa: SLF001

    await device.async_read_device_info(timeout=0.01, force=True)

    device._read_idlock_attributes.assert_not_awaited()  # noqa: SLF001
