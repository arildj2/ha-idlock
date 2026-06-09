"""Persistent local storage for ID Lock integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION

# Delay (seconds) for coalescing event-driven saves into one disk write
SAVE_DELAY = 10


@dataclass
class Slot:
    """A single user slot on a lock (can have PIN, RFID, or both)."""

    slot: int
    label: str = ""
    enabled: bool = True
    has_code: bool = False
    has_rfid: bool = False


@dataclass
class Lock:
    """Stored lock metadata."""

    name: str
    entity_id: str
    device_ieee: str
    max_slots: int = 25
    custom_name: bool = False  # True if the user renamed the lock via the panel
    slots: dict[int, Slot] = field(default_factory=dict)


class IDLockStore:
    """HA storage wrapper for lock metadata and slot labels."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize with HA instance."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY, private=True)
        self.locks: dict[str, Lock] = {}

    async def async_load(self) -> None:
        """Load locks from persistent storage."""
        data = await self._store.async_load()
        if not data:
            self.locks = {}
            return

        self.locks = {}
        for ieee, raw in data.get("locks", {}).items():
            if not isinstance(raw, dict) or not raw.get("entity_id"):
                continue  # skip malformed entries rather than failing setup
            slots: dict[int, Slot] = {}
            for k, v in raw.get("slots", {}).items():
                slots[int(k)] = Slot(
                    slot=int(k),
                    label=v.get("label", ""),
                    enabled=v.get("enabled", True),
                    has_code=v.get("has_code", False),
                    has_rfid=v.get("has_rfid", False),
                )
            self.locks[ieee] = Lock(
                name=raw.get("name") or raw["entity_id"],
                entity_id=raw["entity_id"],
                device_ieee=ieee,
                max_slots=raw.get("max_slots", 25),
                custom_name=raw.get("custom_name", False),
                slots=slots,
            )

    def _data_to_save(self) -> dict[str, Any]:
        """Serialize all lock data for storage."""
        return {
            "locks": {
                ieee: {
                    "name": lock.name,
                    "entity_id": lock.entity_id,
                    "max_slots": lock.max_slots,
                    "custom_name": lock.custom_name,
                    "slots": {
                        str(s.slot): {
                            "label": s.label,
                            "enabled": s.enabled,
                            "has_code": s.has_code,
                            "has_rfid": s.has_rfid,
                        }
                        for s in lock.slots.values()
                    },
                }
                for ieee, lock in self.locks.items()
            },
        }

    async def async_save(self) -> None:
        """Persist all lock data immediately."""
        await self._store.async_save(self._data_to_save())

    def async_schedule_save(self) -> None:
        """Persist lock data after a delay, coalescing rapid changes."""
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    def get_lock(self, ieee: str) -> Lock | None:
        """Get a lock by IEEE address."""
        return self.locks.get(ieee)

    def ensure_slot(self, lock: Lock, slot: int) -> Slot:
        """Get or create a slot on a lock."""
        if slot not in lock.slots:
            lock.slots[slot] = Slot(slot=slot)
        return lock.slots[slot]

    async def async_wipe(self) -> None:
        """Delete all persisted data."""
        self.locks = {}
        await self._store.async_remove()
