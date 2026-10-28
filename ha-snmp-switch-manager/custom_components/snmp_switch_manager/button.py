"""Button entities: "Save Configuration" for RLTech switches.

Port enable/disable, port descriptions and system strings changed via SNMP
only modify the switch's running configuration. Pressing this button writes
the running configuration to flash (same as "Save" in the switch web UI).
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_LEGACY_DEVICE_ID
from .database_builtin import is_rltech_vendor
from .snmp import SwitchSnmpClient

_LOGGER = logging.getLogger(__name__)


def cfgsave_available(client: SwitchSnmpClient) -> bool:
    """True when the switch is an RLTech switch whose firmware has the save object."""
    return is_rltech_vendor(client.cache.get("vendor")) and bool(client.cache.get("cfgsave_supported"))


def cfgsave_entity_plan(hass, client: SwitchSnmpClient, domain: str, unique_ids: list[str]) -> bool:
    """Decide whether the save-configuration entities are created; clean up when confirmed unsupported.

    * supported   -> create.
    * unsupported -> (confirmed: not RLTech, or the switch answered noSuchObject)
                     remove entities left from an earlier setup, do not create.
    * unknown     -> the startup probe failed transiently: keep registered
                     entities (created, shown unavailable until the probe,
                     retried on every poll, succeeds); create nothing new.
    """
    state = client.cfgsave_state()
    ent_reg = er.async_get(hass)
    registered = [ent_reg.async_get_entity_id(domain, DOMAIN, uid) for uid in unique_ids]
    if state == "supported":
        return True
    if state == "unsupported":
        for eid in registered:
            if eid:
                ent_reg.async_remove(eid)
        return False
    return any(registered)


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    client: SwitchSnmpClient = runtime.client
    coordinator = runtime.coordinator

    unique_id = f"{entry.entry_id}-cfgsave-button"
    if not cfgsave_entity_plan(hass, client, "button", [unique_id]):
        return

    hostname = client.cache.get("sysName") or entry.data.get("name") or client.host

    identifiers = {(DOMAIN, entry.entry_id)}
    legacy_device_id = str(
        entry.data.get(CONF_LEGACY_DEVICE_ID) or entry.options.get(CONF_LEGACY_DEVICE_ID) or ""
    ).strip()
    if legacy_device_id:
        identifiers.add((DOMAIN, legacy_device_id))
    device_info = DeviceInfo(identifiers=identifiers, name=hostname)

    async_add_entities([SaveConfigButton(coordinator, client, entry, unique_id, hostname, device_info)])


class SaveConfigButton(CoordinatorEntity, ButtonEntity):
    """Write the running configuration of the switch to flash."""

    _attr_icon = "mdi:content-save"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client: SwitchSnmpClient, entry, unique_id: str, hostname: str, device_info: DeviceInfo):
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = unique_id
        self._attr_name = f"{hostname} Save Configuration"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        # Unavailable while support is not (yet) confirmed, e.g. after a
        # transient probe failure at startup.
        return super().available and cfgsave_available(self._client)

    async def async_press(self) -> None:
        # HomeAssistantError raised by save_config is shown in the frontend.
        try:
            await self._client.save_config(wait=True)
        finally:
            await self.coordinator.async_request_refresh()
