"""Sensor platform for ID Lock integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_LOCKED, STATE_UNLOCKED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

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
        entities.append(IDLockLastPersonSensor(ieee, name, entity_id))

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


class IDLockLastPersonSensor(SensorEntity):
    """Sensor showing who last opened the door, resolved from slot labels."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-lock"

    # Grace period (seconds) to wait for an operation event after a state change.
    # If the lock fires both, the operation event arrives first and sets the person;
    # the state change then arrives within this window and is ignored.
    _STATE_GRACE_SECONDS = 2.0

    def __init__(self, ieee: str, lock_name: str, lock_entity_id: str) -> None:
        """Initialize last person sensor for a lock."""
        self._ieee = ieee
        self._lock_entity_id = lock_entity_id
        self._attr_unique_id = f"{DOMAIN}_{ieee}_last_person"
        self._attr_name = f"{lock_name} last person"
        self._attr_native_value: str | None = None
        self._event_data: dict[str, Any] = {}
        self._unsub_event: Any = None
        self._unsub_state: Any = None
        self._last_event_time: float = 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return event details as attributes."""
        return self._event_data

    def _update_person(
        self,
        person: str,
        operation: str,
        source: str,
        code_slot: int,
    ) -> None:
        """Update sensor state with person info."""
        now = dt_util.now().isoformat()
        self._attr_native_value = person
        self._event_data = {
            "person": person,
            "operation": operation,
            "source": source,
            "code_slot": code_slot,
            "lock_entity_id": self._lock_entity_id,
            "last_changed": now,
        }
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to lock events and state changes."""
        from .storage import IDLockStore

        @callback
        def _handle_event(event: Event) -> None:
            """Handle operation_event_notification with full details."""
            data = event.data
            if data.get("device_ieee") != self._ieee:
                return

            source = data.get("source", "unknown")
            operation = data.get("operation", "unknown")
            code_slot = data.get("code_slot", 0)

            # Resolve person name from slot label in store
            person = ""
            if code_slot > 0:
                store: IDLockStore | None = self.hass.data.get(DOMAIN, {}).get("store")
                if store:
                    lock = store.get_lock(self._ieee)
                    if lock and code_slot in lock.slots:
                        person = lock.slots[code_slot].label

            if not person and code_slot > 0:
                person = f"Slot {code_slot}"
            elif not person:
                person = source

            self._last_event_time = dt_util.utcnow().timestamp()
            self._update_person(person, operation, source, code_slot)

        @callback
        def _handle_state_change(event: Event) -> None:
            """Fallback: update with 'unknown' when lock state changes without an operation event."""
            new_state = event.data.get("new_state")
            old_state = event.data.get("old_state")
            if not new_state or not old_state:
                return

            new_val = new_state.state
            old_val = old_state.state
            if new_val not in (STATE_LOCKED, STATE_UNLOCKED):
                return
            if new_val == old_val:
                return

            # Skip if we just got an operation event within the grace period
            now_ts = dt_util.utcnow().timestamp()
            if now_ts - self._last_event_time < self._STATE_GRACE_SECONDS:
                return

            operation = "unlock" if new_val == STATE_UNLOCKED else "lock"
            self._update_person("unknown", operation, "unknown", 0)

        self._unsub_event = self.hass.bus.async_listen(EVENT_IDLOCK, _handle_event)
        self._unsub_state = async_track_state_change_event(
            self.hass, [self._lock_entity_id], _handle_state_change
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe on removal."""
        if self._unsub_event:
            self._unsub_event()
        if self._unsub_state:
            self._unsub_state()
