"""WebSocket API handlers for ID Lock integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    WS_CLEAR_CODE,
    WS_DISABLE_CODE,
    WS_ENABLE_CODE,
    WS_GET_LOCK,
    WS_LIST_LOCKS,
    WS_READ_ALL_CODES,
    WS_READ_PIN,
    WS_RENAME_CODE,
    WS_SAVE_LOCK_META,
    WS_SET_CODE,
)
from .lock_device import get_device
from .storage import IDLockStore

_LOGGER = logging.getLogger(__name__)

# Slot number must be 1-based, upper bound checked per-lock
SLOT_SCHEMA = vol.All(int, vol.Range(min=1))

# Hardware-specific PIN length is checked after resolving the device.
PIN_CODE_SCHEMA = vol.All(str, vol.Match(r"^\d+$"), vol.Length(max=255))

_BOOLEAN_SETTINGS = {
    "master_pin_mode",
    "relock_enabled",
    "require_pin_for_rf",
    "rfid_enabled",
}
_INTEGER_SETTING_RANGES = {
    "audio_volume": (0, 2),
    "lock_mode": (0, 3),
    "service_pin_mode": (0, 9),
}


def _get_store(hass: HomeAssistant) -> IDLockStore | None:
    """Get the store, or None if not loaded."""
    return hass.data.get(DOMAIN, {}).get("store")


def _validate_slot(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    lock: Any,
    max_slots: int | None = None,
) -> int | None:
    """Validate a slot against discovered hardware or stored capabilities."""
    slot = int(msg["slot"])
    maximum = max_slots if max_slots is not None else lock.max_slots
    if slot > maximum:
        connection.send_error(
            msg["id"], "invalid_slot", f"Slot must be 1-{maximum}"
        )
        return None
    return slot


def _validate_setting_value(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> bool:
    """Validate a device-setting value without truthy string coercion."""
    setting = msg["setting"]
    value = msg["value"]

    if setting in _BOOLEAN_SETTINGS:
        valid = type(value) is bool
        expected = "true or false"
    elif setting in _INTEGER_SETTING_RANGES:
        minimum, maximum = _INTEGER_SETTING_RANGES[setting]
        valid = type(value) is int and minimum <= value <= maximum
        expected = f"an integer from {minimum} to {maximum}"
    else:
        connection.send_error(
            msg["id"], "invalid_setting", f"Unknown setting: {setting}"
        )
        return False

    if not valid:
        connection.send_error(
            msg["id"],
            "invalid_value",
            f"{setting} must be {expected}",
        )
        return False
    return True


async def _get_lock_and_device(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    *,
    require_device: bool = False,
) -> tuple[IDLockStore, Any, Any] | None:
    """Get store + lock + optionally connected device. Sends error on failure."""
    store = _get_store(hass)
    if store is None:
        connection.send_error(msg["id"], "not_ready", "ID Lock store not loaded")
        return None

    lock = store.get_lock(msg["device_ieee"])
    if not lock:
        connection.send_error(msg["id"], "not_found", "Unknown lock")
        return None

    device = None
    if require_device:
        device = get_device(hass, lock.device_ieee)
        if not device.connected:
            success = await device.async_connect()
            if not success:
                connection.send_error(
                    msg["id"],
                    "device_error",
                    "Could not connect to lock (device may be asleep)",
                )
                return None

    return store, lock, device


def _lock_to_dict(lock: Any) -> dict[str, Any]:
    """Serialize a Lock object for the frontend."""
    return {
        "name": lock.name,
        "entity_id": lock.entity_id,
        "device_ieee": lock.device_ieee,
        "max_slots": int(lock.max_slots),
        "slots": {
            str(s.slot): {
                "slot": s.slot,
                "label": s.label,
                "enabled": bool(s.enabled),
                "has_code": bool(s.has_code),
                "has_rfid": bool(s.has_rfid),
            }
            for s in sorted(lock.slots.values(), key=lambda x: x.slot)
        },
    }


# --- List / Get ---


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_LIST_LOCKS})
@websocket_api.async_response
async def ws_list_locks(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List all managed locks."""
    store = _get_store(hass)
    if store is None:
        connection.send_error(msg["id"], "not_ready", "ID Lock store not loaded")
        return
    connection.send_result(msg["id"], [_lock_to_dict(lock) for lock in store.locks.values()])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): WS_GET_LOCK, vol.Required("device_ieee"): str}
)
@websocket_api.async_response
async def ws_get_lock(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Get a single lock by IEEE."""
    result = await _get_lock_and_device(hass, connection, msg)
    if not result:
        return
    _, lock, _ = result
    connection.send_result(msg["id"], _lock_to_dict(lock))


# --- PIN operations (require device) ---


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SET_CODE,
        vol.Required("device_ieee"): str,
        vol.Required("slot"): SLOT_SCHEMA,
        vol.Required("code"): PIN_CODE_SCHEMA,
        vol.Optional("label", default=""): vol.All(str, vol.Length(max=30)),
    }
)
@websocket_api.async_response
async def ws_set_code(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Set a PIN code on a lock slot."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    store, lock, device = result

    slot = _validate_slot(connection, msg, lock, device.num_pin_slots)
    if slot is None:
        return
    code = msg["code"]
    if not device.min_pin_len <= len(code) <= device.max_pin_len:
        connection.send_error(
            msg["id"],
            "invalid_code",
            f"PIN must contain {device.min_pin_len}-{device.max_pin_len} digits",
        )
        return
    success = await device.async_set_pin(slot, code)
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to set code on slot {slot}")
        return

    s = store.ensure_slot(lock, slot)
    s.label = msg["label"]
    s.enabled = True
    s.has_code = True
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): WS_CLEAR_CODE, vol.Required("device_ieee"): str, vol.Required("slot"): SLOT_SCHEMA}
)
@websocket_api.async_response
async def ws_clear_code(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Clear a PIN code from a lock slot."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    store, lock, device = result

    slot = _validate_slot(connection, msg, lock, device.num_pin_slots)
    if slot is None:
        return
    success = await device.async_clear_pin(slot)
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to clear slot {slot}")
        return

    s = store.ensure_slot(lock, slot)
    s.has_code = False
    if not s.has_rfid:
        s.enabled = False
        s.label = ""
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): WS_ENABLE_CODE, vol.Required("device_ieee"): str, vol.Required("slot"): SLOT_SCHEMA}
)
@websocket_api.async_response
async def ws_enable_code(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Enable a code slot."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    store, lock, device = result

    slot = _validate_slot(connection, msg, lock, device.num_pin_slots)
    if slot is None:
        return
    success = await device.async_enable_pin(slot)
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to enable slot {slot}")
        return

    s = store.ensure_slot(lock, slot)
    s.enabled = True
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): WS_DISABLE_CODE, vol.Required("device_ieee"): str, vol.Required("slot"): SLOT_SCHEMA}
)
@websocket_api.async_response
async def ws_disable_code(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Disable a code slot."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    store, lock, device = result

    slot = _validate_slot(connection, msg, lock, device.num_pin_slots)
    if slot is None:
        return
    success = await device.async_disable_pin(slot)
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to disable slot {slot}")
        return

    s = store.ensure_slot(lock, slot)
    s.enabled = False
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


# --- RFID ---


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "idlock/clear_rfid", vol.Required("device_ieee"): str, vol.Required("slot"): SLOT_SCHEMA}
)
@websocket_api.async_response
async def ws_clear_rfid(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Clear an RFID tag from a slot."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    store, lock, device = result

    slot = _validate_slot(connection, msg, lock, device.num_rfid_slots)
    if slot is None:
        return
    success = await device.async_clear_rfid(slot)
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to clear RFID slot {slot}")
        return

    s = store.ensure_slot(lock, slot)
    s.has_rfid = False
    if not s.has_code:
        s.enabled = False
        s.label = ""
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


# --- Metadata (store-only, no device needed) ---


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_RENAME_CODE,
        vol.Required("device_ieee"): str,
        vol.Required("slot"): SLOT_SCHEMA,
        vol.Required("label"): vol.All(str, vol.Length(max=30)),
    }
)
@websocket_api.async_response
async def ws_rename_code(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Rename a code slot label."""
    result = await _get_lock_and_device(hass, connection, msg)
    if not result:
        return
    store, lock, _ = result

    slot = _validate_slot(connection, msg, lock)
    if slot is None:
        return
    s = store.ensure_slot(lock, slot)
    s.label = msg["label"]
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SAVE_LOCK_META,
        vol.Required("device_ieee"): str,
        vol.Optional("name"): vol.All(str, vol.Length(min=1, max=64)),
    }
)
@websocket_api.async_response
async def ws_save_lock_meta(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Update user-editable lock metadata."""
    result = await _get_lock_and_device(hass, connection, msg)
    if not result:
        return
    store, lock, _ = result

    if "name" in msg:
        lock.name = msg["name"]
        # User explicitly renamed via the panel — don't overwrite from the
        # device registry on the next reload (see async_setup_entry).
        lock.custom_name = True
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


# --- Full sync (require device) ---


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): WS_READ_ALL_CODES, vol.Required("device_ieee"): str}
)
@websocket_api.async_response
async def ws_read_all_codes(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Read all PIN + RFID slots from the lock hardware and sync to store."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    store, lock, device = result

    expected = max(device.num_pin_slots, device.num_rfid_slots)
    _LOGGER.info(
        "[IDLock] Reading all %d slots from %s (PIN + RFID)...",
        expected,
        lock.name,
    )
    all_slots = await device.async_read_all_slots()
    _LOGGER.info("[IDLock] Read %d/%d slot responses from %s", len(all_slots), expected, lock.name)

    # Only update store if we got a complete scan — partial data would
    # overwrite previously known slot states for the missing slots.
    if len(all_slots) < expected:
        _LOGGER.warning("[IDLock] Incomplete scan for %s — store not updated", lock.name)
        connection.send_error(
            msg["id"],
            "incomplete_scan",
            f"Lock responded for only {len(all_slots)} of {expected} slots; existing data was preserved",
        )
        return

    found_pins = 0
    found_rfids = 0
    for slot_data in all_slots:
        slot_num = slot_data["slot"]
        s = store.ensure_slot(lock, slot_num)
        s.has_code = slot_data["has_pin"]
        s.has_rfid = slot_data["has_rfid"]
        s.enabled = slot_data["pin_enabled"] or slot_data["rfid_enabled"]
        if slot_data["has_pin"]:
            found_pins += 1
        if slot_data["has_rfid"]:
            found_rfids += 1

    _LOGGER.info(
        "[IDLock] Found %d PINs and %d RFIDs on %s",
        found_pins,
        found_rfids,
        lock.name,
    )
    lock.max_slots = expected
    await store.async_save()

    # Lock is confirmed awake after successful slot scan — try reading settings
    # if we haven't loaded them yet (helps sleepy locks that timeout on cold reads)
    await device.async_try_read_settings_opportunistic()

    connection.send_result(msg["id"], _lock_to_dict(lock))


# --- Device settings (require device) ---


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "idlock/get_device_settings",
        vol.Required("device_ieee"): str,
        vol.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_get_device_settings(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Get IDLock device settings."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    store, lock, device = result
    await device.async_read_device_info(timeout=8.0, force=msg["force"])
    discovered_max = max(device.num_pin_slots, device.num_rfid_slots)
    if lock.max_slots != discovered_max:
        lock.max_slots = discovered_max
        await store.async_save()
    connection.send_result(msg["id"], device.get_device_info())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "idlock/set_device_setting",
        vol.Required("device_ieee"): str,
        vol.Required("setting"): str,
        vol.Required("value"): vol.Any(int, bool, str),
    }
)
@websocket_api.async_response
async def ws_set_device_setting(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Set a single IDLock device setting."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    _, _, device = result

    if not _validate_setting_value(connection, msg):
        return

    setting = msg["setting"]
    setting_handlers = {
        "master_pin_mode": device.async_set_master_pin_mode,
        "rfid_enabled": device.async_set_rfid_enabled,
        "require_pin_for_rf": device.async_set_require_pin_for_rf,
        "service_pin_mode": device.async_set_service_pin_mode,
        "lock_mode": device.async_set_lock_mode,
        "relock_enabled": device.async_set_relock,
        "audio_volume": device.async_set_audio_volume,
    }

    handler = setting_handlers[setting]
    success = await handler(msg["value"])
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to set {setting}")
        return

    connection.send_result(msg["id"], device.get_device_info())


# --- Read single PIN (require device) ---


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): WS_READ_PIN, vol.Required("device_ieee"): str, vol.Required("slot"): SLOT_SCHEMA}
)
@websocket_api.async_response
async def ws_read_pin(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Read a single PIN code from the lock hardware."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    _, lock, device = result

    slot = _validate_slot(connection, msg, lock, device.num_pin_slots)
    if slot is None:
        return
    pin_data = await device.async_get_pin(slot)
    if pin_data is None:
        connection.send_error(msg["id"], "device_error", f"Failed to read slot {slot}")
        return

    # Lock is awake — refresh stale settings in a task owned by this entry.
    entry = hass.data.get(DOMAIN, {}).get("entry")
    if entry is not None:
        entry.async_create_background_task(
            hass,
            device.async_try_read_settings_opportunistic(),
            f"ID Lock settings refresh {device.ieee}",
        )

    connection.send_result(msg["id"], {"slot": slot, "code": pin_data.get("code")})


def register_ws_handlers(hass: HomeAssistant) -> None:
    """Register all WebSocket command handlers."""
    for handler in (
        ws_list_locks,
        ws_get_lock,
        ws_set_code,
        ws_clear_code,
        ws_enable_code,
        ws_disable_code,
        ws_rename_code,
        ws_save_lock_meta,
        ws_read_all_codes,
        ws_read_pin,
        ws_clear_rfid,
        ws_get_device_settings,
        ws_set_device_setting,
    ):
        websocket_api.async_register_command(hass, handler)
