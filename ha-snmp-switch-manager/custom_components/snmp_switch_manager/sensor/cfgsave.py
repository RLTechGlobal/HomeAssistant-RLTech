"""Diagnostic sensors for the RLTech "save configuration" object.

* ConfigSaveStatusSensor: status of the last save started via SNMP
  (idle / in_progress / success / failed). Saves made in the switch web UI
  or CLI are not reflected.
* ConfigLastSavedSensor: time of the last successful save started via SNMP
  since the switch booted (unknown when there was none).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ..const import RLTECH_SAVE_STATUS
from ..database_builtin import is_rltech_vendor

CFGSAVE_STATUS_UID = "cfgsave-status"
CFGSAVE_LAST_UID = "cfgsave-last"


def cfgsave_last_saved(last_ticks, uptime_ticks, now: datetime) -> Optional[datetime]:
    """Absolute time of a save given its sysUpTime stamp and the current sysUpTime.

    Returns None when there was no save since boot (stamp 0) or the stamp is
    newer than the uptime (the switch rebooted in between).
    """
    try:
        last = int(last_ticks)
        uptime = int(uptime_ticks)
    except (TypeError, ValueError):
        return None
    if last <= 0 or last > uptime:
        return None
    return now - timedelta(seconds=(uptime - last) / 100.0)


class _CfgSaveSensorBase(CoordinatorEntity, SensorEntity):
    """Unavailable while save support is not (yet) confirmed by the probe."""

    def __init__(self, coordinator, client):
        super().__init__(coordinator)
        self._client = client

    @property
    def available(self) -> bool:
        return (
            super().available
            and is_rltech_vendor(self._client.cache.get("vendor"))
            and bool(self._client.cache.get("cfgsave_supported"))
        )


class ConfigSaveStatusSensor(_CfgSaveSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(RLTECH_SAVE_STATUS.values())
    _attr_icon = "mdi:content-save-cog"

    def __init__(self, coordinator, entry, device_info: DeviceInfo, hostname: str, client):
        super().__init__(coordinator, client)
        self._attr_unique_id = f"{entry.entry_id}-{CFGSAVE_STATUS_UID}"
        self._attr_name = f"{hostname} Configuration Save Status"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> Optional[str]:
        value = (self.coordinator.data or {}).get("cfgsave_status")
        return value if value in self._attr_options else None


class ConfigLastSavedSensor(_CfgSaveSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:content-save-check"

    def __init__(self, coordinator, entry, device_info: DeviceInfo, hostname: str, client):
        super().__init__(coordinator, client)
        self._attr_unique_id = f"{entry.entry_id}-{CFGSAVE_LAST_UID}"
        self._attr_name = f"{hostname} Configuration Last Saved"
        self._attr_device_info = device_info
        self._last_ticks = None
        self._value: Optional[datetime] = None

    @property
    def native_value(self) -> Optional[datetime]:
        data = self.coordinator.data or {}
        last = data.get("cfgsave_last_ticks")
        uptime = data.get("cfgsave_uptime_ticks")
        # Recompute only when the stamp changes; otherwise every poll would
        # produce a slightly different time (poll jitter) and a new state.
        if last != self._last_ticks or (self._value is None and last):
            self._last_ticks = last
            self._value = cfgsave_last_saved(last, uptime, dt_util.utcnow())
        elif last is not None and uptime is not None and self._value is not None:
            try:
                if int(last) > int(uptime):  # rebooted: stamp no longer valid
                    self._value = None
            except (TypeError, ValueError):
                pass
        return self._value
