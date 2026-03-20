"""Sensor platform for ID Lock integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_LOCKS, DOMAIN, EVENT_IDLOCK, EVENT_IDLOCK_CODE_CHANGED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for each managed lock."""
    cfg_locks: list[dict[str, Any]] = entry.data.get(CONF_LOCKS, [])

    entities: list[SensorEntity] = []
    for lock_cfg in cfg_locks:
        ieee = lock_cfg.get("device_ieee", "")
        name = lock_cfg.get("name", "Lock")
        entity_id = lock_cfg.get("entity_id", "")

        if not ieee:
            continue

        entities.append(IDLockLastEventSensor(ieee, name, entity_id))
        entities.append(IDLockCodeChangeSensor(ieee, name, entity_id))

    if entities:
        async_add_entities(entities)


class IDLockLastEventSensor(SensorEntity):
    """Sensor showing the last lock/unlock event with method and code slot."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:lock-clock"

    def __init__(self, ieee: str, lock_name: str, lock_entity_id: str) -> None:
        """Initialize last event sensor for a lock."""
        self._ieee = ieee
        self._lock_entity_id = lock_entity_id
        self._attr_unique_id = f"{DOMAIN}_{ieee}_last_event"
        self._attr_name = f"{lock_name} last event"
        self._attr_native_value: str | None = None
        self._event_data: dict[str, Any] = {}
        self._unsub: Any = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return event details as attributes."""
        return self._event_data

    async def async_added_to_hass(self) -> None:
        """Subscribe to lock events when added."""

        @callback
        def _handle_event(event: Event) -> None:
            data = event.data
            if data.get("device_ieee") != self._ieee:
                return

            source = data.get("source", "unknown")
            operation = data.get("operation", "unknown")
            code_slot = data.get("code_slot", 0)

            if code_slot > 0:
                self._attr_native_value = f"{operation} via {source} (slot {code_slot})"
            else:
                self._attr_native_value = f"{operation} via {source}"

            self._event_data = {
                "source": source,
                "operation": operation,
                "code_slot": code_slot,
                "lock_entity_id": self._lock_entity_id,
            }
            self.async_write_ha_state()

        self._unsub = self.hass.bus.async_listen(EVENT_IDLOCK, _handle_event)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe on removal."""
        if self._unsub:
            self._unsub()


class IDLockCodeChangeSensor(SensorEntity):
    """Sensor showing the last code programming event.

    Fires when PINs or RFID codes are added/deleted/changed on the lock,
    including changes made via the physical keypad.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:key-plus"

    def __init__(self, ieee: str, lock_name: str, lock_entity_id: str) -> None:
        """Initialize code change sensor for a lock."""
        self._ieee = ieee
        self._lock_entity_id = lock_entity_id
        self._attr_unique_id = f"{DOMAIN}_{ieee}_code_change"
        self._attr_name = f"{lock_name} code change"
        self._attr_native_value: str | None = None
        self._event_data: dict[str, Any] = {}
        self._unsub: Any = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return event details as attributes."""
        return self._event_data

    async def async_added_to_hass(self) -> None:
        """Subscribe to code change events."""

        @callback
        def _handle_event(event: Event) -> None:
            data = event.data
            if data.get("device_ieee") != self._ieee:
                return

            event_type = data.get("event", "unknown")
            source = data.get("source", "unknown")
            code_slot = data.get("code_slot", 0)

            if code_slot > 0:
                self._attr_native_value = f"{event_type} on slot {code_slot} (via {source})"
            else:
                self._attr_native_value = f"{event_type} (via {source})"

            self._event_data = {
                "event": event_type,
                "source": source,
                "code_slot": code_slot,
                "lock_entity_id": self._lock_entity_id,
            }
            self.async_write_ha_state()

        self._unsub = self.hass.bus.async_listen(EVENT_IDLOCK_CODE_CHANGED, _handle_event)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe on removal."""
        if self._unsub:
            self._unsub()
