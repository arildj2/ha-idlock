"""Sidebar panel registration for ID Lock integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import async_register_built_in_panel, async_remove_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    PANEL_ICON,
    PANEL_MODULE_URL,
    PANEL_PATH,
    PANEL_TITLE,
    PANEL_URL_BASE,
    PANEL_URL_PATH,
)


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the sidebar panel and static assets."""
    root = Path(__file__).parent
    panel_dir = root / PANEL_PATH

    domain_data = hass.data.setdefault(DOMAIN, {})

    if not domain_data.get("static_paths_registered"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_URL_BASE, str(panel_dir), True)],
        )
        domain_data["static_paths_registered"] = True

    try:
        async_register_built_in_panel(
            hass,
            component_name="custom",
            frontend_url_path=PANEL_URL_PATH,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            require_admin=True,
            config={
                "_panel_custom": {
                    "name": "ha-idlock-panel",
                    "module_url": PANEL_MODULE_URL,
                },
            },
        )
    except ValueError:
        async_remove_panel(hass, PANEL_URL_PATH)
        async_register_built_in_panel(
            hass,
            component_name="custom",
            frontend_url_path=PANEL_URL_PATH,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            require_admin=True,
            config={
                "_panel_custom": {
                    "name": "ha-idlock-panel",
                    "module_url": PANEL_MODULE_URL,
                },
            },
        )

    domain_data["panel_registered"] = True
