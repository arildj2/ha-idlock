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


def _get_store(hass: HomeAssistant) -> IDLockStore | None:
    """Get the store, or None if not loaded."""
    return hass.data.get(DOMAIN, {}).get("store")


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


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SET_CODE,
        vol.Required("device_ieee"): str,
        vol.Required("slot"): int,
        vol.Required("code"): str,
        vol.Optional("label", default=""): str,
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

    slot = int(msg["slot"])
    success = await device.async_set_pin(slot, msg["code"])
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to set code on slot {slot}")
        return

    s = store.ensure_slot(lock, slot)
    s.label = msg.get("label", "") or s.label
    s.enabled = True
    s.has_code = True
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


@websocket_api.websocket_command(
    {vol.Required("type"): WS_CLEAR_CODE, vol.Required("device_ieee"): str, vol.Required("slot"): int}
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

    slot = int(msg["slot"])
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


@websocket_api.websocket_command(
    {vol.Required("type"): WS_ENABLE_CODE, vol.Required("device_ieee"): str, vol.Required("slot"): int}
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

    slot = int(msg["slot"])
    success = await device.async_enable_pin(slot)
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to enable slot {slot}")
        return

    s = store.ensure_slot(lock, slot)
    s.enabled = True
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


@websocket_api.websocket_command(
    {vol.Required("type"): WS_DISABLE_CODE, vol.Required("device_ieee"): str, vol.Required("slot"): int}
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

    slot = int(msg["slot"])
    success = await device.async_disable_pin(slot)
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to disable slot {slot}")
        return

    s = store.ensure_slot(lock, slot)
    s.enabled = False
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


# --- RFID ---


@websocket_api.websocket_command(
    {vol.Required("type"): "idlock/clear_rfid", vol.Required("device_ieee"): str, vol.Required("slot"): int}
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

    slot = int(msg["slot"])
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_RENAME_CODE,
        vol.Required("device_ieee"): str,
        vol.Required("slot"): int,
        vol.Required("label"): str,
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

    s = store.ensure_slot(lock, int(msg["slot"]))
    s.label = msg["label"]
    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SAVE_LOCK_META,
        vol.Required("device_ieee"): str,
        vol.Optional("name"): str,
        vol.Optional("max_slots"): int,
    }
)
@websocket_api.async_response
async def ws_save_lock_meta(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Update lock metadata (name, max_slots)."""
    result = await _get_lock_and_device(hass, connection, msg)
    if not result:
        return
    store, lock, _ = result

    if "name" in msg:
        lock.name = msg["name"]
    if "max_slots" in msg:
        lock.max_slots = int(msg["max_slots"])

    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


# --- Full sync (require device) ---


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

    _LOGGER.info("[IDLock] Reading all %d slots from %s (PIN + RFID)...", device.num_pin_slots, lock.name)
    all_slots = await device.async_read_all_slots()
    _LOGGER.info("[IDLock] Read %d slot responses from %s", len(all_slots), lock.name)

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

    _LOGGER.info("[IDLock] Found %d PINs and %d RFIDs on %s", found_pins, found_rfids, lock.name)

    # Lock is confirmed awake after successful slot scan — try reading settings
    # if we haven't loaded them yet (helps sleepy locks that timeout on cold reads)
    await device.async_try_read_settings_opportunistic()

    await store.async_save()
    connection.send_result(msg["id"], _lock_to_dict(lock))


# --- Device settings (require device) ---


@websocket_api.websocket_command(
    {vol.Required("type"): "idlock/get_device_settings", vol.Required("device_ieee"): str}
)
@websocket_api.async_response
async def ws_get_device_settings(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Get IDLock device settings."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    _, _, device = result
    # Read attributes if not loaded yet (e.g. first settings panel open)
    if device.mfr_attrs_supported is None:
        await device.async_read_device_info()
    connection.send_result(msg["id"], device.get_device_info())


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

    setting = msg["setting"]
    setting_handlers = {
        "master_pin_mode": lambda v: device.async_set_master_pin_mode(bool(v)),
        "rfid_enabled": lambda v: device.async_set_rfid_enabled(bool(v)),
        "require_pin_for_rf": lambda v: device.async_set_require_pin_for_rf(bool(v)),
        "service_pin_mode": lambda v: device.async_set_service_pin_mode(int(v)),
        "lock_mode": lambda v: device.async_set_lock_mode(int(v)),
        "relock_enabled": lambda v: device.async_set_relock(bool(v)),
        "audio_volume": lambda v: device.async_set_audio_volume(int(v)),
    }

    handler = setting_handlers.get(setting)
    if not handler:
        connection.send_error(msg["id"], "invalid_setting", f"Unknown setting: {setting}")
        return

    success = await handler(msg["value"])
    if not success:
        connection.send_error(msg["id"], "device_error", f"Failed to set {setting}")
        return

    connection.send_result(msg["id"], device.get_device_info())


# --- Read single PIN (require device) ---


@websocket_api.websocket_command(
    {vol.Required("type"): WS_READ_PIN, vol.Required("device_ieee"): str, vol.Required("slot"): int}
)
@websocket_api.async_response
async def ws_read_pin(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Read a single PIN code from the lock hardware."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    _, _, device = result

    slot = int(msg["slot"])
    pin_data = await device.async_get_pin(slot)
    if pin_data is None:
        connection.send_error(msg["id"], "device_error", f"Failed to read slot {slot}")
        return

    # Lock is awake — opportunistically load settings in background
    hass.async_create_task(device.async_try_read_settings_opportunistic())

    connection.send_result(msg["id"], {"slot": slot, "code": pin_data.get("code")})


# --- Debug ---


@websocket_api.websocket_command(
    {
        vol.Required("type"): "idlock/debug_read_slot",
        vol.Required("device_ieee"): str,
        vol.Required("slot"): int,
    }
)
@websocket_api.async_response
async def ws_debug_read_slot(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Debug: read a single slot and return raw zigpy response details."""
    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    _, _, device = result

    raw = await device.async_get_pin_raw(int(msg["slot"]))
    connection.send_result(msg["id"], raw)


@websocket_api.websocket_command(
    {vol.Required("type"): "idlock/debug_read_mfr_attrs", vol.Required("device_ieee"): str}
)
@websocket_api.async_response
async def ws_debug_read_mfr_attrs(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Debug: try multiple strategies to read manufacturer-specific attributes."""
    import asyncio

    result = await _get_lock_and_device(hass, connection, msg, require_device=True)
    if not result:
        return
    _, _, device = result

    from .const import (
        ATTR_AUDIO_VOLUME,
        ATTR_LOCK_FW_VERSION,
        ATTR_LOCK_MODE,
        ATTR_MASTER_PIN_MODE,
        ATTR_RELOCK_ENABLED,
        ATTR_RFID_ENABLED,
        ATTR_SERVICE_PIN_MODE,
        BASIC_CLUSTER_ID,
        IDLOCK_MANUFACTURER_CODE,
    )

    cluster = device._cluster  # noqa: SLF001
    zigpy_dev = device._zigpy_device  # noqa: SLF001
    debug: dict[str, Any] = {"ieee": device.ieee}

    # Find Basic cluster
    basic_cluster = None
    if zigpy_dev:
        for ep_id, ep in zigpy_dev.endpoints.items():
            if ep_id == 0:
                continue
            if BASIC_CLUSTER_ID in ep.in_clusters:
                basic_cluster = ep.in_clusters[BASIC_CLUSTER_ID]
                break

    # Test 1: Standard sound_volume read (should always work)
    try:
        r = await cluster.read_attributes(["sound_volume"])
        attrs = r[0] if isinstance(r, (list, tuple)) else r
        debug["test1_sound_volume"] = repr(attrs.get("sound_volume"))
    except Exception as e:  # noqa: BLE001
        debug["test1_sound_volume_error"] = f"{type(e).__name__}: {e}"

    # Test 2: All mfr attrs at once with manufacturer code (current approach)
    all_mfr = [ATTR_MASTER_PIN_MODE, ATTR_RFID_ENABLED, ATTR_SERVICE_PIN_MODE,
               ATTR_LOCK_MODE, ATTR_RELOCK_ENABLED, ATTR_AUDIO_VOLUME]
    try:
        r = await asyncio.wait_for(
            cluster._read_attributes(all_mfr, manufacturer=IDLOCK_MANUFACTURER_CODE),  # noqa: SLF001
            timeout=10,
        )
        debug["test2_all_mfr_with_code"] = _parse_raw_records(r)
    except Exception as e:  # noqa: BLE001
        debug["test2_all_mfr_with_code_error"] = f"{type(e).__name__}: {e}"

    # Test 3: Single mfr attr (0x4000) with manufacturer code
    try:
        r = await asyncio.wait_for(
            cluster._read_attributes([ATTR_MASTER_PIN_MODE], manufacturer=IDLOCK_MANUFACTURER_CODE),  # noqa: SLF001
            timeout=10,
        )
        debug["test3_single_0x4000_with_code"] = _parse_raw_records(r)
    except Exception as e:  # noqa: BLE001
        debug["test3_single_0x4000_with_code_error"] = f"{type(e).__name__}: {e}"

    # Test 4: Single mfr attr (0x4000) WITHOUT manufacturer code
    try:
        r = await asyncio.wait_for(
            cluster._read_attributes([ATTR_MASTER_PIN_MODE]),  # noqa: SLF001
            timeout=10,
        )
        debug["test4_single_0x4000_no_code"] = _parse_raw_records(r)
    except Exception as e:  # noqa: BLE001
        debug["test4_single_0x4000_no_code_error"] = f"{type(e).__name__}: {e}"

    # Test 5: Lock firmware from Basic cluster 0x5000
    if basic_cluster:
        try:
            r = await asyncio.wait_for(
                basic_cluster._read_attributes([ATTR_LOCK_FW_VERSION], manufacturer=IDLOCK_MANUFACTURER_CODE),  # noqa: SLF001
                timeout=10,
            )
            debug["test5_lock_fw_0x5000"] = _parse_raw_records(r)
        except Exception as e:  # noqa: BLE001
            debug["test5_lock_fw_0x5000_error"] = f"{type(e).__name__}: {e}"

    # Test 6: Basic cluster build_id (0x4000) — standard, no mfr code
    if basic_cluster:
        try:
            r = await basic_cluster.read_attributes(["build_id"])
            attrs = r[0] if isinstance(r, (list, tuple)) else r
            debug["test6_basic_build_id"] = repr(attrs.get("build_id"))
        except Exception as e:  # noqa: BLE001
            debug["test6_basic_build_id_error"] = f"{type(e).__name__}: {e}"

    connection.send_result(msg["id"], debug)


def _parse_raw_records(raw_result: Any) -> dict[str, Any]:
    """Parse raw attribute read result into a debug-friendly dict."""
    parsed: dict[str, Any] = {}
    if not isinstance(raw_result, (list, tuple)) or not raw_result:
        return {"raw": repr(raw_result)[:300]}
    for record in raw_result[0]:
        aid = getattr(record, "attrid", None)
        status = getattr(record, "status", None)
        value_obj = getattr(record, "value", None)
        val = getattr(value_obj, "value", None) if value_obj else None
        key = f"0x{aid:04X}" if aid is not None else repr(aid)
        parsed[key] = {"status": repr(status), "value": repr(val)}
    return parsed


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
        ws_debug_read_slot,
        ws_debug_read_mfr_attrs,
    ):
        websocket_api.async_register_command(hass, handler)
