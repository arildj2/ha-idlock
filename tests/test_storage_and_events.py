"""Tests for persistent state and one-based programming events."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from homeassistant.core import callback

from custom_components.ha_idlock import (
    _handle_operation_event,
    _handle_programming_event,
)
from custom_components.ha_idlock.const import EVENT_IDLOCK, EVENT_IDLOCK_CODE_CHANGED
from custom_components.ha_idlock.storage import IDLockStore, Lock


async def test_load_preserves_valid_legacy_data_and_skips_malformed(hass) -> None:
    """Existing v1 storage remains readable without accepting invalid slots."""
    store = IDLockStore(hass)
    store._store.async_load = AsyncMock(  # noqa: SLF001
        return_value={
            "locks": {
                "valid": {
                    "name": "Front door",
                    "entity_id": "lock.front_door",
                    "max_slots": "25",
                    "slots": {
                        "1": {"label": "Alice", "has_code": True},
                        "0": {"label": "invalid"},
                        "26": {"label": "invalid"},
                        "bad": {"label": "invalid"},
                    },
                },
                "broken": "not a mapping",
            }
        }
    )

    await store.async_load()

    assert list(store.locks) == ["valid"]
    assert list(store.locks["valid"].slots) == [1]
    assert store.locks["valid"].slots[1].label == "Alice"


async def test_programming_event_updates_one_based_slot_and_fires_event(hass) -> None:
    """A keypad event updates exactly the physical one-based user slot."""
    store = IDLockStore(hass)
    store.async_schedule_save = Mock()
    lock = Lock("Front door", "lock.front_door", "ieee")
    store.locks["ieee"] = lock
    received = []
    hass.bus.async_listen(EVENT_IDLOCK_CODE_CHANGED, lambda event: received.append(event.data))

    _handle_programming_event(
        hass,
        store,
        lock,
        "ieee",
        {"source": "keypad", "event": "PinAdded", "code_slot": 3},
    )
    await hass.async_block_till_done()

    assert lock.slots[3].has_code is True
    assert 2 not in lock.slots
    assert received[0]["code_slot"] == 3
    store.async_schedule_save.assert_called_once()


async def test_new_zha_operation_events_from_real_locks(hass) -> None:
    """Payloads recorded from ID Lock 150/202 on ZHA 2026.9 map correctly.

    New ZHA adds 1 to the raw user ID: manual/auto events (raw 0) arrive as
    code_slot 1, and master PIN unlocks (raw 109) arrive as code_slot 110.
    """
    lock = Lock("Front door", "lock.front_door", "ieee")
    received = []
    hass.bus.async_listen(EVENT_IDLOCK, callback(lambda event: received.append(event.data)))

    for args in (
        {"source": "Keypad", "operation": "Unlock", "code_slot": 110},
        {"source": "Keypad", "operation": "Unlock", "code_slot": 4},
        {"source": "Manual", "operation": "AutoLock", "code_slot": 1},
        {"source": "Manual", "operation": "Manual_Unlock", "code_slot": 1},
        {"source": "Keypad", "operation": "OnTouchLock", "code_slot": 1},
        {"source": "Keypad", "operation": "UnlockFailureInvalidPINorID", "code_slot": 1},
        {"source": "RF", "operation": "Unlock", "code_slot": 1},
        {"operation_event_source": 0, "operation_event_code": 1, "user_id": 3},
        {"operation_event_source": 0, "operation_event_code": 1, "user_id": 109},
    ):
        _handle_operation_event(hass, lock, "ieee", args)
    await hass.async_block_till_done()

    assert [
        (e["source"], e["operation"], e["code_slot"], e["credential"]) for e in received
    ] == [
        ("keypad", "unlock", 0, "master_pin"),
        ("keypad", "unlock", 3, "pin"),
        ("manual", "auto_lock", 0, None),
        ("manual", "manual_unlock", 0, None),
        ("keypad", "on_touch_lock", 0, None),
        ("keypad", "unlock_failure_invalid_pin_or_id", 0, None),
        ("rf", "unlock", 0, None),
        ("keypad", "unlock", 3, "pin"),
        ("keypad", "unlock", 0, "master_pin"),
    ]
