"""ID Lock Manager — Home Assistant integration for Datek ID Lock via ZHA."""

from __future__ import annotations

import contextlib
import importlib
import logging
from typing import Any

from homeassistant.components.frontend import async_remove_panel as remove_panel
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)

from .const import (
    CONF_LOCKS,
    DOMAIN,
    DOOR_LOCK_CLUSTER_ID,
    EVENT_IDLOCK,
    EVENT_IDLOCK_CODE_CHANGED,
    EVENT_ZHA,
    PANEL_URL_PATH,
    PLATFORMS,
    PROG_EVENT_MASTER_CODE_CHANGED,
    PROG_EVENT_PIN_ADDED,
    PROG_EVENT_PIN_CHANGED,
    PROG_EVENT_PIN_DELETED,
    PROG_EVENT_RFID_ADDED,
    PROG_EVENT_RFID_DELETED,
)
from .lock_device import get_device
from .panel import async_register_panel
from .storage import IDLockStore, Lock
from .websocket import register_ws_handlers

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _get_friendly_name(hass: HomeAssistant, entity_id: str) -> str | None:
    """Get the user-facing name for a lock entity. Prefers user-set device name."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    ent = ent_reg.async_get(entity_id)
    if not ent:
        return None

    device = dev_reg.async_get(ent.device_id) if ent.device_id else None
    if not device:
        return None

    return device.name_by_user or ent.original_name or device.name


# Source value → string mapping
_SOURCE_MAP = {0: "keypad", 1: "rf", 2: "manual", 3: "rfid", 0xFF: "unknown"}

# Operation event code → string mapping
_OPERATION_MAP = {0: "lock", 1: "unlock", 2: "toggle", 9: "auto_lock"}

# Programming event code → string mapping
_PROG_EVENT_MAP = {
    PROG_EVENT_MASTER_CODE_CHANGED: "master_code_changed",
    PROG_EVENT_PIN_ADDED: "pin_added",
    PROG_EVENT_PIN_DELETED: "pin_deleted",
    PROG_EVENT_PIN_CHANGED: "pin_changed",
    PROG_EVENT_RFID_ADDED: "rfid_added",
    PROG_EVENT_RFID_DELETED: "rfid_deleted",
}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration (config entry only)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ID Lock Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Load persistent store
    store = IDLockStore(hass)
    await store.async_load()
    hass.data[DOMAIN]["store"] = store
    hass.data[DOMAIN]["entry"] = entry

    # Seed locks from config entry
    cfg_locks: list[dict[str, Any]] = entry.data.get(CONF_LOCKS, [])
    selected_ieees: set[str] = set()

    for item in cfg_locks:
        entity_id = item.get("entity_id")
        device_ieee = item.get("device_ieee")
        name = item.get("name")
        max_slots = int(item.get("max_slots", 25))

        if not (entity_id and device_ieee and name):
            continue

        selected_ieees.add(device_ieee)

        # Resolve friendly name from device registry (user-set name preferred)
        friendly_name = _get_friendly_name(hass, entity_id) or name

        if device_ieee not in store.locks:
            store.locks[device_ieee] = Lock(
                name=friendly_name,
                entity_id=entity_id,
                device_ieee=device_ieee,
                max_slots=max_slots,
            )
        else:
            # Update name on every load to pick up renames
            store.locks[device_ieee].name = friendly_name

    # Prune deselected locks
    to_delete = [ieee for ieee in list(store.locks) if ieee not in selected_ieees]
    for ieee in to_delete:
        del store.locks[ieee]

    # Clean up empty slots from store (leftover from previous offset bug)
    for lock in store.locks.values():
        empty_slots = [num for num, s in lock.slots.items() if not s.has_code and not s.has_rfid]
        for num in empty_slots:
            del lock.slots[num]

    await store.async_save()

    # All device work runs in background — never block startup
    # Each lock runs independently so a dead lock doesn't block others.
    async def _setup_single_lock(ieee: str, lock: Lock) -> None:
        """Connect to one device and do initial reads."""
        device = get_device(hass, ieee)
        try:
            await device.async_connect()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("[IDLock] Could not connect to %s", ieee)
            return

        # Read device capabilities (sends Zigbee commands, may be slow)
        if device.connected:
            await device.async_read_device_info()

        # First setup: read all slots from lock
        if not lock.slots and device.connected:
            _LOGGER.info("[IDLock] First setup for %s — reading slots", lock.name)
            all_slots = await device.async_read_all_slots()
            for slot_data in all_slots:
                s = store.ensure_slot(lock, slot_data["slot"])
                s.has_code = slot_data["has_pin"]
                s.has_rfid = slot_data["has_rfid"]
                s.enabled = slot_data["pin_enabled"] or slot_data["rfid_enabled"]
            await store.async_save()
            _LOGGER.info("[IDLock] Initial read complete for %s", lock.name)

    async def _background_setup() -> None:
        """Connect to all devices concurrently with per-lock timeout."""
        import asyncio

        PER_LOCK_TIMEOUT = 60  # seconds — fail fast for dead locks

        tasks = []
        for ieee, lock in store.locks.items():
            coro = asyncio.wait_for(
                _setup_single_lock(ieee, lock),
                timeout=PER_LOCK_TIMEOUT,
            )
            tasks.append((ieee, coro))

        for ieee, task in tasks:
            try:
                await task
            except TimeoutError:
                _LOGGER.warning(
                    "[IDLock] Setup timed out for %s (lock may be out of batteries)", ieee
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning("[IDLock] Setup failed for %s", ieee, exc_info=True)

    hass.async_create_task(_background_setup())

    # Register WebSocket API (once)
    if not hass.data[DOMAIN].get("ws_registered"):
        register_ws_handlers(hass)
        hass.data[DOMAIN]["ws_registered"] = True

    # Register sidebar panel
    await async_register_panel(hass)

    # Listen for ZHA lock events
    @callback
    def _handle_zha_event(event: Any) -> None:
        data = event.data or {}
        device_ieee = data.get("device_ieee")
        if not device_ieee or device_ieee not in store.locks:
            return

        # Only process DoorLock cluster events
        if data.get("cluster_id") != DOOR_LOCK_CLUSTER_ID:
            return

        command = data.get("command")
        args = data.get("args", {})
        lock = store.locks[device_ieee]

        if command == "operation_event_notification":
            _LOGGER.debug("[IDLock] operation_event args: %s", args)
            _handle_operation_event(hass, lock, device_ieee, args)
        elif command == "programming_event_notification":
            _LOGGER.debug("[IDLock] programming_event args: %s", args)
            _handle_programming_event(hass, store, lock, device_ieee, args)

    unsub = hass.bus.async_listen(EVENT_ZHA, _handle_zha_event)
    hass.data[DOMAIN]["unsub_zha_event"] = unsub

    # Pre-import platform modules in the executor so async_forward_entry_setups
    # doesn't trigger a blocking import_module call inside the event loop.
    await hass.async_add_import_executor_job(
        importlib.import_module, "custom_components.ha_idlock.sensor"
    )

    # Forward to entity platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _handle_operation_event(
    hass: HomeAssistant,
    lock: Lock,
    device_ieee: str,
    args: dict[str, Any],
) -> None:
    """Handle operation_event_notification (lock/unlock events)."""
    # ZHA ≥2025.x uses string keys: source, operation, code_slot
    # Older ZHA uses integer keys: operation_event_source, operation_event_code, user_id
    if "source" in args:
        source = str(args["source"]).lower()
        operation = str(args.get("operation", "unknown")).lower()
        raw_slot = args.get("code_slot")
    else:
        source = _parse_value(args.get("operation_event_source"), _SOURCE_MAP)
        operation = _parse_value(args.get("operation_event_code"), _OPERATION_MAP)
        raw_slot = args.get("user_id")

    code_slot = 0
    if raw_slot is not None and source in ("keypad", "rfid"):
        try:
            code_slot = int(raw_slot)  # IDLock reports 1-based slot numbers
        except (ValueError, TypeError):
            code_slot = 0

    hass.bus.async_fire(
        EVENT_IDLOCK,
        {
            "lock_name": lock.name,
            "entity_id": lock.entity_id,
            "device_ieee": device_ieee,
            "source": source,
            "operation": operation,
            "code_slot": code_slot,
        },
    )


def _handle_programming_event(
    hass: HomeAssistant,
    store: IDLockStore,
    lock: Lock,
    device_ieee: str,
    args: dict[str, Any],
) -> None:
    """Handle programming_event_notification (code add/delete/change events).

    Fired by the lock when codes are modified via keypad, RF, or other means.
    Updates our store to stay in sync without polling.
    """
    # ZHA ≥2025.x uses string keys: source, event, code_slot
    # Older ZHA uses integer keys: program_event_source, program_event_code, user_id
    if "source" in args:
        source = str(args["source"]).lower()
        event_code_raw = args.get("event") or args.get("program_event_code")
        user_id = args.get("code_slot") if args.get("code_slot") is not None else args.get("user_id")
    else:
        source = _parse_value(args.get("program_event_source"), _SOURCE_MAP)
        event_code_raw = args.get("program_event_code")
        user_id = args.get("user_id")
    # New ZHA may send string event names (e.g. "PinAdded") instead of int codes
    _PROG_EVENT_STR_MAP = {
        "mastercodechanged": (PROG_EVENT_MASTER_CODE_CHANGED, "master_code_changed"),
        "pinadded": (PROG_EVENT_PIN_ADDED, "pin_added"),
        "pindeleted": (PROG_EVENT_PIN_DELETED, "pin_deleted"),
        "pinchanged": (PROG_EVENT_PIN_CHANGED, "pin_changed"),
        "rfidadded": (PROG_EVENT_RFID_ADDED, "rfid_added"),
        "rfiddeleted": (PROG_EVENT_RFID_DELETED, "rfid_deleted"),
    }
    if isinstance(event_code_raw, str) and not event_code_raw.isdigit():
        matched = _PROG_EVENT_STR_MAP.get(event_code_raw.lower().replace("_", ""), (0, event_code_raw.lower()))
        event_code = matched[0]
        event_name = matched[1]
    else:
        try:
            event_code = int(event_code_raw) if event_code_raw is not None else 0
        except (ValueError, TypeError):
            event_code = 0
        event_name = _PROG_EVENT_MAP.get(event_code, "unknown")

    # IDLock reports 1-based slot numbers in events
    slot = 0
    if user_id is not None:
        try:
            slot = int(user_id)
        except (ValueError, TypeError):
            slot = 0

    # Update our local store based on the event
    if slot > 0:
        s = store.ensure_slot(lock, slot)
        if event_code in (PROG_EVENT_PIN_ADDED, PROG_EVENT_PIN_CHANGED):
            s.has_code = True
            s.enabled = True
            _LOGGER.info(
                "[IDLock] %s: PIN %s on slot %d (via %s)",
                lock.name,
                event_name,
                slot,
                source,
            )
        elif event_code == PROG_EVENT_PIN_DELETED:
            s.has_code = False
            s.enabled = False
            s.label = ""
            _LOGGER.info(
                "[IDLock] %s: PIN deleted from slot %d (via %s)",
                lock.name,
                slot,
                source,
            )
        elif event_code == PROG_EVENT_RFID_ADDED:
            s.has_rfid = True
            _LOGGER.info(
                "[IDLock] %s: RFID added on slot %d (via %s)",
                lock.name,
                slot,
                source,
            )
        elif event_code == PROG_EVENT_RFID_DELETED:
            s.has_rfid = False
            if not s.has_code:
                s.enabled = False
                s.label = ""
            _LOGGER.info(
                "[IDLock] %s: RFID deleted from slot %d (via %s)",
                lock.name,
                slot,
                source,
            )

        # Persist the change
        hass.async_create_task(store.async_save())

    # Fire event for automations
    hass.bus.async_fire(
        EVENT_IDLOCK_CODE_CHANGED,
        {
            "lock_name": lock.name,
            "entity_id": lock.entity_id,
            "device_ieee": device_ieee,
            "event": event_name,
            "source": source,
            "code_slot": slot,
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    with contextlib.suppress(Exception):
        remove_panel(hass, PANEL_URL_PATH)

    if unsub := hass.data.get(DOMAIN, {}).pop("unsub_zha_event", None):
        unsub()

    # Clear cached device connections so reload gets fresh ones
    hass.data.get(DOMAIN, {}).pop("devices", None)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal: wipe stored data."""
    with contextlib.suppress(Exception):
        remove_panel(hass, PANEL_URL_PATH)

    store: IDLockStore | None = hass.data.get(DOMAIN, {}).get("store")
    if store is None:
        store = IDLockStore(hass)
        await store.async_load()
    await store.async_wipe()

    hass.data.pop(DOMAIN, None)


def _parse_value(value: Any, mapping: dict[int, str]) -> str:
    """Parse a ZHA event value using a mapping dict."""
    if isinstance(value, int):
        return mapping.get(value, "unknown")
    try:
        return mapping.get(int(value), "unknown")
    except (ValueError, TypeError):
        return str(value).lower() if value else "unknown"
