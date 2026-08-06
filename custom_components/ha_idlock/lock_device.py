"""Direct zigpy DoorLock cluster access for IDLock devices."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from zigpy.types import EUI64
from zigpy.types import uint8_t as zigpy_uint8
from zigpy.types import uint16_t as zigpy_uint16
from zigpy.zcl.foundation import Attribute, TypeValue

from .const import (
    ATTR_AUDIO_VOLUME,
    ATTR_LOCK_FW_VERSION,
    ATTR_LOCK_MODE,
    ATTR_MASTER_PIN_MODE,
    ATTR_RELOCK_ENABLED,
    ATTR_RFID_ENABLED,
    ATTR_SERVICE_PIN_MODE,
    BASIC_CLUSTER_ID,
    DEFAULT_MAX_PIN_LEN,
    DEFAULT_MIN_PIN_LEN,
    DEFAULT_NUM_PIN_SLOTS,
    DEFAULT_NUM_RFID_SLOTS,
    DOMAIN,
    DOOR_LOCK_CLUSTER_ID,
    IDLOCK_MANUFACTURER_CODE,
    ZHA_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SETTINGS_CACHE_TTL = 300.0


class IDLockDevice:
    """Wraps a zigpy DoorLock cluster for direct PIN management.

    Provides high-level methods for PIN operations.

    IDLock uses 1-based slot numbering natively (both in commands and events),
    so no offset conversion is needed — slot numbers pass through as-is.

    IDLock 202 specifics:
    - 25 PIN slots (1-25), 25 RFID slots
    - PIN length 4-10 digits
    - Manufacturer attributes at 0x4000-0x4006
    """

    def __init__(self, hass: HomeAssistant, ieee: str) -> None:
        """Initialize with HA instance and device IEEE address."""
        self.hass = hass
        self.ieee = ieee
        self._cluster: Any = None
        self._zigpy_device: Any = None

        # Standard DoorLock capabilities
        self.num_pin_slots: int = DEFAULT_NUM_PIN_SLOTS
        self.num_rfid_slots: int = DEFAULT_NUM_RFID_SLOTS
        self.max_pin_len: int = DEFAULT_MAX_PIN_LEN
        self.min_pin_len: int = DEFAULT_MIN_PIN_LEN

        # Firmware versions (from Basic cluster)
        self.lock_firmware: str | None = None      # 0x5000: lock firmware (e.g. "1.5.0")
        self.module_build: str | None = None       # 0x4000: Zigbee module build (e.g. "0.7")

        # IDLock manufacturer-specific settings
        # ID Lock is a sleepy battery device. Keep every request to one lock in
        # a single queue so panel tabs, automations, and wake events cannot race.
        self._operation_lock = asyncio.Lock()
        self._settings_updated_at: float | None = None
        self.mfr_attrs_supported: bool | None = None  # None=unknown, True/False after first read
        self.master_pin_mode: bool | None = None
        self.rfid_enabled: bool | None = None
        self.require_pin_for_rf: bool | None = None
        self.service_pin_mode: int | None = None
        self.lock_mode: int | None = None
        self.relock_enabled: bool | None = None
        self.audio_volume: int | None = None

    @property
    def connected(self) -> bool:
        """Return True if cluster is available."""
        return self._cluster is not None

    @property
    def info_loaded(self) -> bool:
        """Return True if device info has been read at least once."""
        return self.mfr_attrs_supported is not None

    async def async_connect(self) -> bool:
        """Connect to the zigpy device and locate the DoorLock cluster.

        This only does local lookups (no Zigbee traffic) so it's instant.
        Attribute reads are done lazily on first use or via async_read_device_info().
        """
        gateway = _get_zha_gateway(self.hass)
        if not gateway:
            _LOGGER.error("[IDLock] ZHA gateway not available")
            return False

        try:
            eui64 = EUI64.convert(self.ieee)
            self._zigpy_device = gateway.application_controller.get_device(ieee=eui64)
        except (ValueError, KeyError, AttributeError) as e:
            _LOGGER.error("[IDLock] Can't find device %s: %s", self.ieee, e)
            return False

        if not self._zigpy_device:
            _LOGGER.error("[IDLock] Device not found: %s", self.ieee)
            return False

        self._cluster = _find_door_lock_cluster(self._zigpy_device)
        if not self._cluster:
            _LOGGER.error("[IDLock] No DoorLock cluster on %s", self.ieee)
            return False

        _LOGGER.debug("[IDLock] Connected to %s", self.ieee)
        return True

    def settings_are_fresh(self) -> bool:
        """Return whether cached settings are still within their TTL."""
        if self._settings_updated_at is None:
            return False
        return (
            asyncio.get_running_loop().time() - self._settings_updated_at
            < SETTINGS_CACHE_TTL
        )

    async def async_read_device_info(
        self, timeout: float = 15.0, *, force: bool = False
    ) -> None:
        """Read device info within one overall timeout and operation queue."""
        if not force and self.settings_are_fresh():
            return
        async with self._operation_lock:
            if not force and self.settings_are_fresh():
                return
            try:
                async with asyncio.timeout(timeout):
                    await self._read_firmware_versions()
                    await self._read_capabilities()
                    await self._read_idlock_attributes()
            except TimeoutError:
                _LOGGER.warning(
                    "[IDLock] Timed out reading device info for %s after %.1fs "
                    "(lock may be asleep)",
                    self.ieee,
                    timeout,
                )
            _LOGGER.debug(
                "[IDLock] Read device info for %s (fw=%s, module=%s, %d PIN slots, %d RFID slots, PIN %d-%d digits)",
                self.ieee,
                self.lock_firmware,
                self.module_build,
                self.num_pin_slots,
                self.num_rfid_slots,
                self.min_pin_len,
                self.max_pin_len,
            )

    async def _read_firmware_versions(self) -> None:
        """Read lock firmware and Zigbee module version from Basic cluster.

        Basic cluster 0x5000 (mfr-specific): Lock firmware (e.g. "1.5.0")
        Basic cluster 0x4000 (standard): Build id / Zigbee module version (e.g. "0.7")
        """
        basic_cluster = None
        if self._zigpy_device:
            for ep_id, ep in self._zigpy_device.endpoints.items():
                if ep_id == 0:
                    continue
                if BASIC_CLUSTER_ID in ep.in_clusters:
                    basic_cluster = ep.in_clusters[BASIC_CLUSTER_ID]
                    break

        if not basic_cluster:
            _LOGGER.debug("[IDLock] No Basic cluster found on %s", self.ieee)
            return

        # Read standard build_id (0x4000) — Zigbee module version
        try:
            result = await basic_cluster.read_attributes(["build_id"])
            attrs = result[0] if isinstance(result, (list, tuple)) else result
            if "build_id" in attrs and attrs["build_id"] is not None:
                self.module_build = str(attrs["build_id"])
        except Exception:  # noqa: BLE001
            _LOGGER.debug("[IDLock] Could not read build_id for %s", self.ieee)

        # Read manufacturer-specific lock firmware (0x5000) with Datek mfr code
        try:
            raw_result = await basic_cluster.read_attributes_raw(
                [ATTR_LOCK_FW_VERSION],
                manufacturer=IDLOCK_MANUFACTURER_CODE,
            )
            records = raw_result[0] if isinstance(raw_result, (list, tuple)) else []
            for record in records:
                aid = getattr(record, "attrid", None)
                status = getattr(record, "status", None)
                value_obj = getattr(record, "value", None)
                val = getattr(value_obj, "value", None) if value_obj else None
                if aid == ATTR_LOCK_FW_VERSION and status == 0 and val is not None:
                    self.lock_firmware = str(val)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("[IDLock] Could not read lock firmware version for %s", self.ieee)

        _LOGGER.debug(
            "[IDLock] %s: lock_firmware=%s, module_build=%s",
            self.ieee,
            self.lock_firmware,
            self.module_build,
        )

    async def _read_capabilities(self) -> None:
        """Read standard DoorLock capability attributes."""
        try:
            result = await self._cluster.read_attributes(
                [
                    "num_of_pin_users_supported",
                    "num_of_total_users_supported",
                    "num_of_rfid_users_supported",
                    "max_pin_len",
                    "min_pin_len",
                    "require_pin_for_rf_operation",
                ],
            )
            attrs = result[0] if isinstance(result, (list, tuple)) else result

            if attrs.get("num_of_pin_users_supported"):
                self.num_pin_slots = int(attrs["num_of_pin_users_supported"])
            elif attrs.get("num_of_total_users_supported"):
                self.num_pin_slots = int(attrs["num_of_total_users_supported"])

            if attrs.get("num_of_rfid_users_supported"):
                self.num_rfid_slots = int(attrs["num_of_rfid_users_supported"])

            if attrs.get("max_pin_len"):
                self.max_pin_len = int(attrs["max_pin_len"])
            if attrs.get("min_pin_len"):
                self.min_pin_len = int(attrs["min_pin_len"])
            if "require_pin_for_rf_operation" in attrs:
                self.require_pin_for_rf = bool(attrs["require_pin_for_rf_operation"])
        except Exception:  # noqa: BLE001
            _LOGGER.debug("[IDLock] Could not read capabilities for %s", self.ieee)

    async def _read_idlock_attributes(self) -> None:
        """Read IDLock manufacturer-specific attributes (0x4000-0x4006).

        These require the Datek manufacturer code (4919) to be passed.
        Also reads the standard sound_volume (0x0024) as fallback for audio.

        The caller owns the overall timeout for this group of reads.
        """
        # Read standard sound_volume first (known to work from diagnostic)
        try:
            result = await self._cluster.read_attributes(["sound_volume"])
            attrs = result[0] if isinstance(result, (list, tuple)) else result
            if "sound_volume" in attrs and attrs["sound_volume"] is not None:
                self.audio_volume = int(attrs["sound_volume"])
        except Exception:  # noqa: BLE001
            pass

        # read_attributes_raw is zigpy's public API for attributes which are not
        # registered on the cluster class.
        mfr_attr_ids = [
            ATTR_MASTER_PIN_MODE,
            ATTR_RFID_ENABLED,
            ATTR_SERVICE_PIN_MODE,
            ATTR_LOCK_MODE,
            ATTR_RELOCK_ENABLED,
            ATTR_AUDIO_VOLUME,
        ]
        try:
            raw_result = await self._cluster.read_attributes_raw(
                mfr_attr_ids,
                manufacturer=IDLOCK_MANUFACTURER_CODE,
            )
            # _read_attributes returns (list_of_records, ...) — parse attribute records
            records = raw_result[0] if isinstance(raw_result, (list, tuple)) else []
            attrs: dict[int, Any] = {}
            for record in records:
                aid = getattr(record, "attrid", None)
                status = getattr(record, "status", None)
                value_obj = getattr(record, "value", None)
                val = getattr(value_obj, "value", None) if value_obj else None
                if aid is not None and status == 0 and val is not None:
                    attrs[aid] = val

            _LOGGER.debug("[IDLock] %s: raw mfr attrs=%s", self.ieee, attrs)

            if ATTR_MASTER_PIN_MODE in attrs:
                self.master_pin_mode = bool(attrs[ATTR_MASTER_PIN_MODE])
            if ATTR_RFID_ENABLED in attrs:
                self.rfid_enabled = bool(attrs[ATTR_RFID_ENABLED])
            if ATTR_SERVICE_PIN_MODE in attrs:
                self.service_pin_mode = int(attrs[ATTR_SERVICE_PIN_MODE])
            if ATTR_LOCK_MODE in attrs:
                self.lock_mode = int(attrs[ATTR_LOCK_MODE])
            if ATTR_RELOCK_ENABLED in attrs:
                self.relock_enabled = bool(attrs[ATTR_RELOCK_ENABLED])
            # Manufacturer audio_volume (0x4006, 0-5 scale) is only a fallback:
            # the UI reads and writes the standard sound_volume (0x0024, 0-2),
            # so the standard value must win when both are available.
            if ATTR_AUDIO_VOLUME in attrs and self.audio_volume is None:
                self.audio_volume = int(attrs[ATTR_AUDIO_VOLUME])

            self.mfr_attrs_supported = bool(attrs)
            self._settings_updated_at = asyncio.get_running_loop().time()

            _LOGGER.debug(
                "[IDLock] %s: master_pin=%s, rfid=%s, service_pin=%s, "
                "lock_mode=%s, relock=%s, volume=%s",
                self.ieee,
                self.master_pin_mode,
                self.rfid_enabled,
                self.service_pin_mode,
                self.lock_mode,
                self.relock_enabled,
                self.audio_volume,
            )
        except Exception:  # noqa: BLE001
            # A sleeping lock is not evidence that these attributes are
            # unsupported. Keep the state unknown so a later panel open retries.
            self.mfr_attrs_supported = None
            _LOGGER.warning(
                "[IDLock] Could not read manufacturer attributes for %s (lock may be asleep)",
                self.ieee,
            )

    async def async_try_read_settings_opportunistic(self) -> bool:
        """Try reading settings with a short timeout — call when lock is known awake.

        Returns True if mfr attributes were successfully read.
        Skips while the five-minute cache is fresh.
        """
        if self.settings_are_fresh():
            return self.mfr_attrs_supported is True
        if not self._cluster:
            return False
        _LOGGER.debug("[IDLock] %s: opportunistic settings read (lock should be awake)", self.ieee)
        await self.async_read_device_info(timeout=8.0)
        return self.mfr_attrs_supported is True

    def get_device_info(self) -> dict[str, Any]:
        """Return all device info for the panel/diagnostics."""
        return {
            "ieee": self.ieee,
            "connected": self.connected,
            "info_loaded": self.info_loaded,
            "lock_firmware": self.lock_firmware,
            "module_build": self.module_build,
            "mfr_attrs_supported": self.mfr_attrs_supported,
            "num_pin_slots": self.num_pin_slots,
            "num_rfid_slots": self.num_rfid_slots,
            "max_pin_len": self.max_pin_len,
            "min_pin_len": self.min_pin_len,
            "master_pin_mode": self.master_pin_mode,
            "rfid_enabled": self.rfid_enabled,
            "require_pin_for_rf": self.require_pin_for_rf,
            "service_pin_mode": self.service_pin_mode,
            "lock_mode": self.lock_mode,
            "relock_enabled": self.relock_enabled,
            "audio_volume": self.audio_volume,
        }

    async def async_get_pin(self, slot: int) -> dict[str, Any] | None:
        """Read a PIN from a 1-based slot. Returns dict with status/code or None."""
        async with self._operation_lock:
            return await self._async_get_pin_unlocked(slot)

    async def _async_get_pin_unlocked(self, slot: int) -> dict[str, Any] | None:
        """Read one PIN while the caller holds the device operation lock."""
        if not self._cluster:
            return None

        try:
            resp = await self._cluster.get_pin_code(slot)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("[IDLock] Failed to read slot %d: %s", slot, e)
            return None

        user_status = getattr(resp, "user_status", None)
        code_value = getattr(resp, "code", None)

        status_int = int(user_status) if user_status is not None else 0
        in_use = status_int in (1, 3)

        code_str: str | None = None
        if code_value is not None:
            if isinstance(code_value, bytes):
                code_str = code_value.decode("utf-8", errors="replace") if code_value else None
            elif isinstance(code_value, str):
                code_str = code_value or None
            else:
                code_str = str(code_value) if code_value else None

        return {
            "slot": slot,
            "in_use": in_use,
            "enabled": status_int == 1,
            "code": code_str,
        }

    async def async_set_pin(self, slot: int, code: str) -> bool:
        """Set and read back a PIN. IDLock uses 1-based slot numbers."""
        if not self._cluster:
            return False

        async with self._operation_lock:
            try:
                result = await self._cluster.set_pin_code(
                    slot,
                    1,  # UserStatus.OccupiedEnabled
                    0,  # UserType.Unrestricted
                    code,
                )
                if not _command_result_succeeded(result):
                    _LOGGER.error(
                        "[IDLock] Set PIN slot %d rejected: result=%r", slot, result
                    )
                    return False
                readback = await self._async_get_pin_unlocked(slot)
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("[IDLock] Failed to set PIN slot %d: %s", slot, e)
                return False

            verified = bool(
                readback
                and readback["in_use"]
                and readback["enabled"]
                and readback["code"] == code
            )
            if not verified:
                _LOGGER.error("[IDLock] PIN slot %d failed read-back verification", slot)
            return verified

    async def async_clear_pin(self, slot: int) -> bool:
        """Clear a PIN from a slot."""
        if not self._cluster:
            return False

        async with self._operation_lock:
            try:
                result = await self._cluster.clear_pin_code(slot)
                if not _command_result_succeeded(result):
                    _LOGGER.error(
                        "[IDLock] Clear PIN slot %d rejected: result=%r", slot, result
                    )
                    return False
                readback = await self._async_get_pin_unlocked(slot)
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("[IDLock] Failed to clear PIN slot %d: %s", slot, e)
                return False
            return bool(readback is not None and not readback["in_use"])

    async def async_enable_pin(self, slot: int) -> bool:
        """Enable a PIN slot."""
        if not self._cluster:
            return False

        async with self._operation_lock:
            try:
                result = await self._cluster.set_user_status(slot, 1)  # Enabled
                if not _command_result_succeeded(result):
                    return False
                readback = await self._async_get_pin_unlocked(slot)
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("[IDLock] Failed to enable slot %d: %s", slot, e)
                return False
            return bool(readback and readback["in_use"] and readback["enabled"])

    async def async_disable_pin(self, slot: int) -> bool:
        """Disable a PIN slot."""
        if not self._cluster:
            return False

        async with self._operation_lock:
            try:
                result = await self._cluster.set_user_status(slot, 3)  # Disabled
                if not _command_result_succeeded(result):
                    return False
                readback = await self._async_get_pin_unlocked(slot)
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("[IDLock] Failed to disable slot %d: %s", slot, e)
                return False
            return bool(readback and readback["in_use"] and not readback["enabled"])

    async def async_get_rfid(self, slot: int) -> dict[str, Any] | None:
        """Read an RFID slot. Returns dict with status or None."""
        async with self._operation_lock:
            return await self._async_get_rfid_unlocked(slot)

    async def _async_get_rfid_unlocked(self, slot: int) -> dict[str, Any] | None:
        """Read one RFID slot while the caller holds the operation lock."""
        if not self._cluster:
            return None

        try:
            resp = await self._cluster.get_rfid_code(slot)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("[IDLock] Failed to read RFID slot %d: %s", slot, e)
            return None

        user_status = getattr(resp, "user_status", None)
        status_int = int(user_status) if user_status is not None else 0

        return {
            "slot": slot,
            "in_use": status_int in (1, 3),
            "enabled": status_int == 1,
        }

    async def async_clear_rfid(self, slot: int) -> bool:
        """Clear an RFID tag from a slot."""
        if not self._cluster:
            return False

        async with self._operation_lock:
            try:
                result = await self._cluster.clear_rfid_code(slot)
                if not _command_result_succeeded(result):
                    _LOGGER.error(
                        "[IDLock] Clear RFID slot %d rejected: result=%r", slot, result
                    )
                    return False
                readback = await self._async_get_rfid_unlocked(slot)
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("[IDLock] Failed to clear RFID slot %d: %s", slot, e)
                return False
            return bool(readback is not None and not readback["in_use"])

    async def async_read_all_slots(self, per_slot_timeout: float = 10.0) -> list[dict[str, Any]]:
        """Read all PIN and RFID slots from the lock hardware."""
        async with self._operation_lock:
            return await self._async_read_all_slots_unlocked(per_slot_timeout)

    async def _async_read_all_slots_unlocked(
        self, per_slot_timeout: float
    ) -> list[dict[str, Any]]:
        """Read all credentials while holding the device operation lock."""
        results: list[dict[str, Any]] = []
        total_slots = max(self.num_pin_slots, self.num_rfid_slots)
        empty_credential = {"in_use": False, "enabled": False}
        for slot in range(1, total_slots + 1):
            pin_data = empty_credential
            if slot <= self.num_pin_slots:
                try:
                    pin_data = await asyncio.wait_for(
                        self._async_get_pin_unlocked(slot), timeout=per_slot_timeout
                    )
                except TimeoutError:
                    _LOGGER.warning(
                        "[IDLock] Timeout reading PIN slot %d on %s — aborting scan",
                        slot,
                        self.ieee,
                    )
                    break

            rfid_data = empty_credential
            if slot <= self.num_rfid_slots:
                try:
                    rfid_data = await asyncio.wait_for(
                        self._async_get_rfid_unlocked(slot), timeout=per_slot_timeout
                    )
                except TimeoutError:
                    _LOGGER.warning(
                        "[IDLock] Timeout reading RFID slot %d on %s — aborting scan",
                        slot,
                        self.ieee,
                    )
                    break

            # A None response means the read failed (e.g. delivery error) —
            # abort rather than record the slot as empty, so the partial-scan
            # guard in ws_read_all_codes keeps the store untouched.
            if pin_data is None or rfid_data is None:
                _LOGGER.warning("[IDLock] Failed reading slot %d on %s — aborting scan", slot, self.ieee)
                break

            results.append({
                "slot": slot,
                "has_pin": pin_data["in_use"],
                "pin_enabled": pin_data["enabled"],
                "has_rfid": rfid_data["in_use"],
                "rfid_enabled": rfid_data["enabled"],
            })
        return results

    async def async_read_all_pins(self, per_slot_timeout: float = 10.0) -> list[dict[str, Any]]:
        """Read all PIN slots from the lock hardware (legacy, PIN-only)."""
        async with self._operation_lock:
            results: list[dict[str, Any]] = []
            for slot in range(1, self.num_pin_slots + 1):
                try:
                    pin_data = await asyncio.wait_for(
                        self._async_get_pin_unlocked(slot), timeout=per_slot_timeout
                    )
                except TimeoutError:
                    _LOGGER.warning(
                        "[IDLock] Timeout reading PIN slot %d on %s — aborting scan",
                        slot,
                        self.ieee,
                    )
                    break
                if pin_data:
                    results.append(pin_data)
            return results

    async def _write_mfr_attribute(self, attr_id: int, value: Any) -> bool:
        """Write a manufacturer-specific attribute to the lock.

        Constructs the raw ZCL frame and sends via the cluster's request method,
        bypassing zigpy's attribute lookup which doesn't know about IDLock
        custom attributes (0x4000+).
        """
        if not self._cluster:
            return False

        # Determine ZCL type: boolean (0x10) for toggles, uint8 (0x20) for others
        if attr_id in (ATTR_MASTER_PIN_MODE, ATTR_RFID_ENABLED, ATTR_RELOCK_ENABLED):
            zcl_type = 0x10  # ZCL Boolean
        else:
            zcl_type = 0x20  # ZCL uint8

        attr = Attribute(
            attrid=zigpy_uint16(attr_id),
            value=TypeValue(type=zcl_type, value=zigpy_uint8(int(value))),
        )

        try:
            # write_attributes_raw is the public zigpy API for attributes not
            # registered on the cluster class.
            result = await asyncio.wait_for(
                self._cluster.write_attributes_raw(
                    [attr],
                    manufacturer=IDLOCK_MANUFACTURER_CODE,
                ),
                timeout=10.0,
            )

            if not _write_result_succeeded(result):
                _LOGGER.error(
                    "[IDLock] Write attr 0x%04X rejected: result=%r",
                    attr_id,
                    result,
                )
                return False

            _LOGGER.debug("[IDLock] Wrote attr 0x%04X = %s", attr_id, value)
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "[IDLock] Failed to write attr 0x%04X = %s: %s: %s",
                attr_id,
                value,
                type(e).__name__,
                e,
            )
            return False
        else:
            return True

    async def async_set_master_pin_mode(self, enabled: bool) -> bool:
        """Enable or disable master PIN for unlocking."""
        async with self._operation_lock:
            if await self._write_mfr_attribute(ATTR_MASTER_PIN_MODE, int(enabled)):
                self.master_pin_mode = enabled
                return True
            return False

    async def async_set_rfid_enabled(self, enabled: bool) -> bool:
        """Enable or disable RFID unlocking."""
        async with self._operation_lock:
            if await self._write_mfr_attribute(ATTR_RFID_ENABLED, int(enabled)):
                self.rfid_enabled = enabled
                return True
            return False

    async def async_set_require_pin_for_rf(self, enabled: bool) -> bool:
        """Enable or disable requiring PIN for RF operations."""
        async with self._operation_lock:
            if not self._cluster:
                return False
            try:
                result = await asyncio.wait_for(
                    self._cluster.write_attributes(
                        {"require_pin_for_rf_operation": int(enabled)}
                    ),
                    timeout=10.0,
                )
                if not _write_result_succeeded(result):
                    _LOGGER.error(
                        "[IDLock] Require-PIN write rejected: result=%r", result
                    )
                    return False
                self.require_pin_for_rf = enabled
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("[IDLock] Failed to set require PIN for RF: %s", e)
                return False
            else:
                return True

    async def async_set_audio_volume(self, volume: int) -> bool:
        """Set audio volume (0=silent, 1=low, 2=high)."""
        async with self._operation_lock:
            if not self._cluster:
                return False
            if not 0 <= volume <= 2:
                return False
            try:
                result = await asyncio.wait_for(
                    self._cluster.write_attributes({"sound_volume": volume}),
                    timeout=10.0,
                )
                if not _write_result_succeeded(result):
                    _LOGGER.error(
                        "[IDLock] Audio-volume write rejected: result=%r", result
                    )
                    return False
                self.audio_volume = volume
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("[IDLock] Failed to set audio volume: %s", e)
                return False
            else:
                return True

    async def async_set_relock(self, enabled: bool) -> bool:
        """Enable or disable auto-relock."""
        async with self._operation_lock:
            if await self._write_mfr_attribute(ATTR_RELOCK_ENABLED, int(enabled)):
                self.relock_enabled = enabled
                return True
            return False

    async def async_set_lock_mode(self, mode: int) -> bool:
        """Set lock mode (0-3: auto-lock/away-mode combinations)."""
        async with self._operation_lock:
            if not 0 <= mode <= 3:
                return False
            if await self._write_mfr_attribute(ATTR_LOCK_MODE, mode):
                self.lock_mode = mode
                return True
            return False

    async def async_set_service_pin_mode(self, mode: int) -> bool:
        """Set service PIN mode (0-9, see const.py for values)."""
        async with self._operation_lock:
            if not 0 <= mode <= 9:
                return False
            if await self._write_mfr_attribute(ATTR_SERVICE_PIN_MODE, mode):
                self.service_pin_mode = mode
                return True
            return False


def _get_zha_gateway(hass: HomeAssistant) -> Any:
    """Get the ZHA gateway from hass.data."""
    try:
        zha_data = hass.data[ZHA_DOMAIN]
        gateway = zha_data.gateway_proxy.gateway
    except (KeyError, AttributeError):
        return None
    else:
        return gateway


def _write_result_succeeded(result: Any) -> bool:
    """Return whether a zigpy attribute write response contains only successes."""
    if not isinstance(result, (list, tuple)) or not result:
        return False

    records = result[0]
    if not isinstance(records, (list, tuple)):
        records = [records]
    if not records:
        return False

    for record in records:
        status = getattr(record, "status", record)
        try:
            if int(status) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _command_result_succeeded(result: Any) -> bool:
    """Return whether a DoorLock command received a successful ZCL response."""
    status = getattr(result, "status", None)
    if status is not None:
        try:
            return int(status) == 0
        except (TypeError, ValueError):
            return False

    if isinstance(result, (list, tuple)):
        for item in result:
            item_status = getattr(item, "status", None)
            if item_status is not None:
                try:
                    return int(item_status) == 0
                except (TypeError, ValueError):
                    return False

        # Some zigpy versions expose DefaultResponse as [command_id, status].
        if len(result) == 2:
            try:
                return int(result[1]) == 0
            except (TypeError, ValueError):
                return False

    return False


def _find_door_lock_cluster(zigpy_device: Any) -> Any:
    """Find the DoorLock cluster on a zigpy device."""
    ep1 = zigpy_device.endpoints.get(1)
    if ep1 and DOOR_LOCK_CLUSTER_ID in ep1.in_clusters:
        return ep1.in_clusters[DOOR_LOCK_CLUSTER_ID]

    for ep_id, endpoint in zigpy_device.endpoints.items():
        if ep_id == 0:
            continue
        if DOOR_LOCK_CLUSTER_ID in endpoint.in_clusters:
            return endpoint.in_clusters[DOOR_LOCK_CLUSTER_ID]

    return None


def get_device(hass: HomeAssistant, ieee: str) -> IDLockDevice:
    """Get or create an IDLockDevice instance for an IEEE address."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    devices: dict[str, IDLockDevice] = domain_data.setdefault("devices", {})

    if ieee not in devices:
        devices[ieee] = IDLockDevice(hass, ieee)

    return devices[ieee]
