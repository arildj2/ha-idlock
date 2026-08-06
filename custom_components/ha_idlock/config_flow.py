"""Config flow for ID Lock integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import CONF_LOCKS, DEFAULT_NUM_PIN_SLOTS, DOMAIN


def _entity_to_lock_dict(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """Build lock descriptor from a ZHA lock entity."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    ent = ent_reg.async_get(entity_id)
    if not ent or ent.domain != "lock" or ent.platform != "zha":
        return None

    device = dev_reg.async_get(ent.device_id) if ent.device_id else None
    if not device:
        return None

    ieee: str | None = None
    for idt in device.identifiers:
        if idt[0] == "zha":
            ieee = idt[1]
            break

    if not ieee:
        return None

    return {
        "name": device.name_by_user or ent.original_name or device.name or ent.entity_id,
        "entity_id": ent.entity_id,
        "device_ieee": ieee,
        "max_slots": DEFAULT_NUM_PIN_SLOTS,
    }


def _entity_id_for_ieee(hass: HomeAssistant, ieee: str) -> str | None:
    """Find the current ZHA lock entity id after an entity-registry rename."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    wanted = str(ieee)
    for ent in ent_reg.entities.values():
        if ent.domain != "lock" or ent.platform != "zha" or not ent.device_id:
            continue
        device = dev_reg.async_get(ent.device_id)
        if device and any(
            namespace == "zha" and str(identifier) == wanted
            for namespace, identifier in device.identifiers
        ):
            return ent.entity_id
    return None


class IDLockConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for ID Lock Manager."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return IDLockOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select ZHA lock entities."""
        # The integration uses global state (store, panel, event listener),
        # so only a single config entry is supported.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            selected: list[str] = user_input.get(CONF_LOCKS, [])
            locks: list[dict[str, Any]] = []
            for entity_id in selected:
                lock_dict = _entity_to_lock_dict(self.hass, entity_id)
                if lock_dict:
                    locks.append(lock_dict)

            if locks:
                return self.async_create_entry(
                    title="ID Lock Manager",
                    data={CONF_LOCKS: locks},
                )
            return self.async_show_form(
                step_id="user",
                data_schema=self._locks_schema(),
                errors={"base": "no_valid_locks"},
            )

        return self.async_show_form(step_id="user", data_schema=self._locks_schema())

    @staticmethod
    def _locks_schema(default: list[str] | None = None) -> vol.Schema:
        """Build the ZHA lock selector schema."""
        return vol.Schema(
            {
                vol.Required(CONF_LOCKS, default=default or vol.UNDEFINED): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="lock",
                        integration="zha",
                        multiple=True,
                    ),
                ),
            },
        )


class IDLockOptionsFlow(config_entries.OptionsFlow):
    """Options flow: add or remove locks."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options."""
        stored_locks: list[dict[str, Any]] = self.config_entry.data.get(CONF_LOCKS, [])
        default_entities = [
            current
            for lock in stored_locks
            if (current := _entity_id_for_ieee(self.hass, lock.get("device_ieee", "")))
        ]

        if user_input is not None:
            selected: list[str] = user_input.get(CONF_LOCKS, [])
            new_locks: list[dict[str, Any]] = []
            for entity_id in selected:
                lock_dict = _entity_to_lock_dict(self.hass, entity_id)
                if lock_dict:
                    new_locks.append(lock_dict)

            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_LOCKS: new_locks},
            )

            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id),
            )
            return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCKS, default=default_entities): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="lock",
                        integration="zha",
                        multiple=True,
                    ),
                ),
            },
        )
        return self.async_show_form(step_id="init", data_schema=schema)
