"""Tests for linking sensors to the ZHA lock device (HA 2026.8+ registry)."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_idlock import _async_remove_orphan_devices
from custom_components.ha_idlock.const import CONF_LOCKS, DOMAIN
from custom_components.ha_idlock.sensor import async_setup_entry

IEEE = "68:0a:e2:ff:fe:6b:3a:9b"


async def test_sensors_link_to_zha_device_and_duplicates_are_removed(hass) -> None:
    """Sensors join ZHA's device; our emptied duplicate device is removed."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    zha_entry = MockConfigEntry(domain="zha")
    zha_entry.add_to_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOCKS: [{"device_ieee": IEEE, "entity_id": "lock.hybel"}]},
    )
    entry.add_to_hass(hass)

    zha_device = dev_reg.async_get_or_create(
        config_entry_id=zha_entry.entry_id, identifiers={("zha", IEEE)}
    )
    ent_reg.async_get_or_create(
        "lock", "zha", "lock-uid", config_entry=zha_entry,
        device_id=zha_device.id, suggested_object_id="hybel",
    )
    # Duplicate left behind by the registry split, still holding a sensor.
    duplicate = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, IEEE)}
    )
    sensor_entry = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{IEEE}_last_event", config_entry=entry,
        device_id=duplicate.id,
    )

    # Removing too early must not delete the still-attached sensor.
    _async_remove_orphan_devices(hass, entry)
    assert dev_reg.async_get(duplicate.id) is not None
    assert ent_reg.async_get(sensor_entry.entity_id) is not None

    added = []
    await async_setup_entry(hass, entry, added.extend)
    assert {sensor.device_entry.id for sensor in added} == {zha_device.id}

    # Entity platform re-points the registry entry when the entity is added.
    ent_reg.async_update_entity(sensor_entry.entity_id, device_id=zha_device.id)
    _async_remove_orphan_devices(hass, entry)
    assert dev_reg.async_get(duplicate.id) is None
    assert ent_reg.async_get(sensor_entry.entity_id).device_id == zha_device.id
