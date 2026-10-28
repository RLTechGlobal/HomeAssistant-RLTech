from __future__ import annotations

import asyncio
import time
import logging
import os
import json
from typing import Any, Dict, Optional, List

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .features.cpu import poll_cpu
from .features.memory import poll_memory
from .features.interfaces import poll_interfaces
from .features.ipv4 import poll_ipv4
from .features.power import poll_power
from .features.fans import poll_fans
from .features.psu import poll_psu
from .features.temperature import poll_temperature
from .features.entity_sensor import poll_entity_sensor_fallback
from .features.bandwidth import poll_bandwidth
from .features.poe import poll_poe
from .features.h3c import poll_h3c_environment
from .features.engine import ensure_engine
from .features.device_info import initialize_device_info, refresh_device_info
from .features.auth import build_auth_data, build_write_auth_data
from .database_builtin import is_rltech_vendor, merge_builtin_all

from .snmp_compat import (
    UdpTransportTarget,
    ContextData,
    _do_get_one,
    _do_get_many,
    _do_next_walk,
    _do_bulk_walk,
    _do_set_alias,
    _do_set_admin_status,
    _do_set_poe_admin,
    _do_set_poe_priority,
    _do_set_system_string,
    _do_set_integer,
    SnmpAuthError,
    SnmpConnectionError,
)


# Canonical OIDs from const.py (original repo)
from .const import (
    OID_sysDescr,
    OID_sysName,
    OID_sysUpTime,
    OID_sysContact,
    OID_sysLocation,
    CONF_BW_INCLUDE_STARTS_WITH,
    CONF_BW_INCLUDE_CONTAINS,
    CONF_BW_INCLUDE_ENDS_WITH,
    CONF_BW_EXCLUDE_STARTS_WITH,
    CONF_BW_EXCLUDE_CONTAINS,
    CONF_BW_EXCLUDE_ENDS_WITH,
    CONF_ENV_ENABLE,
    CONF_ENV_MODE,
    CONF_ENV_POLL_INTERVAL,
    ENV_MODE_ATTRIBUTES,
    DEFAULT_ENV_POLL_INTERVAL,
    OID_rltMwCfgSaveAction,
    OID_rltMwCfgSaveStatus,
    OID_rltMwCfgSaveLastTime,
    RLTECH_SAVE_ACTION_SAVE,
    RLTECH_SAVE_STATUS,
)

_LOGGER = logging.getLogger(__name__)

# How long a heavy poll that is already running may delay a save request.
_CFGSAVE_WAIT_POLL_IDLE = 15.0
# Interval between status reads while waiting for a save to complete.
_CFGSAVE_STATUS_INTERVAL = 1.0


def _to_int(value: Any) -> Optional[int]:
    """int() of an SNMP value rendered as text; None for noSuchObject & co."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------- client ----------

class SwitchSnmpClient:
    """SNMP client using PySNMP v7 asyncio API."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        snmp_settings: Dict[str, Any],
        custom_oids: Optional[Dict[str, str]] = None,
        bandwidth_options: Optional[Dict[str, Any]] = None,
        poe_options: Optional[Dict[str, Any]] = None,
        env_options: Optional[Dict[str, Any]] = None,
        feature_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.hass = hass
        self.host = host
        self._snmp_settings = dict(snmp_settings or {})
        self.port = int(self._snmp_settings.get("port") or 161)
        self.custom_oids: Dict[str, str] = dict(custom_oids or {})
        self.feature_overrides: Dict[str, Any] = dict(feature_overrides or {})
        self._database: Dict[str, Any] = {}

        # Bandwidth sensor options (set by config entry options)
        self._bandwidth_options: Dict[str, Any] = dict(bandwidth_options or {})
        
        def _clean_list(key: str) -> tuple[str, ...]:
            return tuple(str(s).strip().lower() for s in (self._bandwidth_options.get(key, []) or []) if str(s).strip())

        self._bw_include_starts = _clean_list(CONF_BW_INCLUDE_STARTS_WITH)
        self._bw_include_contains = _clean_list(CONF_BW_INCLUDE_CONTAINS)
        self._bw_include_ends = _clean_list(CONF_BW_INCLUDE_ENDS_WITH)
        self._bw_exclude_starts = _clean_list(CONF_BW_EXCLUDE_STARTS_WITH)
        self._bw_exclude_contains = _clean_list(CONF_BW_EXCLUDE_CONTAINS)
        self._bw_exclude_ends = _clean_list(CONF_BW_EXCLUDE_ENDS_WITH)

        self._poe_options = poe_options or {}
        self._poe_last_poll: float = 0.0

        self._env_options = env_options or {}
        self._env_last_poll: float = 0.0
        self._bw_last_poll = None  # monotonic timestamp of last bandwidth counter poll
        self._bw_use_hc: Optional[bool] = None
        self._bw_last: Dict[int, Dict[str, Any]] = {}

        self.engine = None
        self.target = None
        self._target_args = ((host, self.port),)
        self._target_kwargs = dict(timeout=1.5, retries=1)

        # SNMP auth/security model (v2c community or v3 USM)
        self.auth_data = self._build_auth_data(self._snmp_settings)
        # Credentials for SET requests (v2c write community, falls back to auth_data)
        self.write_auth_data = build_write_auth_data(self._snmp_settings)
        self.context = ContextData()

        self.cache: Dict[str, Any] = {
            "sysDescr": None,
            "sysName": None,
            "sysUpTime": None,
            "ifTable": {},
            "ipIndex": {},
            "ipMask": {},
            "manufacturer": None,
            "model": None,
            "firmware": None,
        }

        # sysUpTime updates continuously; to avoid excessive churn in Home
        # Assistant, we throttle polling separately from the main coordinator.
        # These are used by async_poll().
        self._last_uptime_poll: float = 0.0
        self._uptime_poll_interval: float = 300.0

        # IPv4 address data rarely changes; throttle refreshes independently.
        self._last_ipv4_poll: float = 0.0
        self._ipv4_poll_interval: float = 300.0

        # Vendor-specific firmware/model OIDs (CBS350, Zyxel, MikroTik) never
        # change at runtime; fetch once during async_initialize and skip on polls.
        self._vendor_oids_fetched: bool = False

        # RLTech "save configuration" (RLTECH-MW-MIB rltMwConfig).
        # While a save runs the switch's configuration database is busy and
        # GETs of ifTable & co. stall until it is done, so heavy polling is
        # paused (monotonic deadline, 0 = not busy) and a save waits for a
        # heavy poll already in flight (_heavy_idle) before it starts.
        self._cfgsave_lock = asyncio.Lock()
        self._cfgsave_busy_until: float = 0.0
        self._cfgsave_probed: bool = False
        self._heavy_idle = asyncio.Event()
        self._heavy_idle.set()
        self.cache["cfgsave_supported"] = False

    def _load_database(self) -> None:
        """Load OID database from JSON files."""
        self._database = {}
        db_path = os.path.join(os.path.dirname(__file__), "database")
        if not os.path.exists(db_path):
            _LOGGER.warning("Database directory not found: %s", db_path)
            return
        
        for filename in os.listdir(db_path):
            if filename.endswith(".json"):
                key = filename[:-5]
                try:
                    with open(os.path.join(db_path, filename), "r") as f:
                        self._database[key] = json.load(f)
                except Exception as e:
                    _LOGGER.error("Failed to load database file %s: %s", filename, e)

        # Built-in vendor entries (e.g. RLTech) are merged in memory so that
        # they keep working even if an automatic update replaced the files.
        self._database = merge_builtin_all(self._database)

    def _get_vendor_info(self) -> Dict[str, Any]:
        """Determine vendor metadata dynamically based on sysObjectID or sysDescr."""
        sys_obj_id = self.cache.get("sysObjectID") or ""
        sys_descr = self.cache.get("sysDescr") or ""
        vendors_db = self._database.get("vendors", {}).get("vendors", [])

        # 1. Match by sysObjectID prefix first (on an arc boundary, so that
        #    e.g. prefix "1.3.6.1.4.1.9" does not match "1.3.6.1.4.1.99...")
        sys_obj_id_n = sys_obj_id.lstrip(".")
        for v in vendors_db:
            prefix = str(v.get("sys_object_id_prefix") or "").lstrip(".")
            if prefix and (sys_obj_id_n == prefix or sys_obj_id_n.startswith(prefix + ".")):
                return v

        # 2. Match by keywords in sysDescr or sysObjectID
        for v in vendors_db:
            keywords = v.get("keywords", [])
            for kw in keywords:
                if kw and (kw.lower() in sys_descr.lower() or kw.lower() in sys_obj_id.lower()):
                    return v

        # 3. Match by name in sysDescr
        for v in vendors_db:
            name = v.get("name")
            if name and name.lower() in sys_descr.lower():
                return v

        # Fallback to standard
        for v in vendors_db:
            if v.get("name") == "Standard":
                return v

        return {"name": "Unknown"}

    def _get_vendor(self) -> str:
        """Determine vendor based on sysObjectID from database."""
        return self._get_vendor_info().get("name", "Unknown")

    def _get_database_oids(self, feature: str, vendor: str) -> List[Dict[str, Any]]:
        """Get OIDs for a specific feature and vendor from database."""
        # Check for feature overrides
        if feature in self.feature_overrides:
            override = self.feature_overrides[feature]
            # Ensure vendors list is present for compatibility
            if "vendors" not in override:
                override["vendors"] = [vendor]
            return [override]
            
        db = self._database.get(feature, {}).get(feature, [])
        vendor_lower = vendor.lower()
        return [item for item in db if any(v.lower() == vendor_lower for v in item.get("vendors", []))]

    def _custom_oid(self, key: str) -> Optional[str]:
        val = (self.custom_oids or {}).get(key)
        if not val:
            return None
        v = str(val).strip()
        if not v:
            return None
        if v.startswith("."):
            v = v[1:]
        return v

    @staticmethod
    def _build_auth_data(settings: Dict[str, Any]):
        """Build pysnmp authData object — delegates to features/auth.py."""
        return build_auth_data(settings)


    def set_uptime_poll_interval(self, seconds: float | int) -> None:
        """Set the sysUpTime throttling interval (seconds)."""
        try:
            val = float(seconds)
        except Exception:
            val = 300.0
        # Guard against non-positive or NaN values
        if not (val > 0):
            val = 300.0
        self._uptime_poll_interval = val

    async def _ensure_engine(self) -> None:
        await ensure_engine(self)

    async def _ensure_target(self) -> None:
        if self.target is None:
            self.target = await UdpTransportTarget.create(*self._target_args, **self._target_kwargs)

    # ---------- lifecycle / fetch ----------

    async def async_close(self) -> None:
        """Release pysnmp resources (transport handles, background tasks).

        Must be called from async_unload_entry to prevent resource leaks on
        integration reload or reconfiguration.
        """
        if self.engine is None:
            return
        try:
            # pysnmp 7.x (v3arch asyncio): close the transport dispatcher.
            dispatcher = getattr(self.engine, "transportDispatcher", None)
            if dispatcher is not None:
                if hasattr(dispatcher, "closeDispatcher"):
                    dispatcher.closeDispatcher()
        except Exception:
            pass
        finally:
            self.engine = None
            self.target = None

    async def async_initialize(self) -> None:
        await self.hass.async_add_executor_job(self._load_database)
        await self._ensure_engine()
        await self._ensure_target()

        # Issue a warm-up GET to flush any remaining lazy pysnmp internal state and verify auth/connection.
        # This will propagate any SnmpAuthError directly to notify Home Assistant on invalid credentials.
        await self._async_get_one(OID_sysDescr)

    async def _async_get_one(self, oid: str) -> Optional[str]:
        await self._ensure_engine()
        await self._ensure_target()
        return await _do_get_one(self.engine, self.auth_data, self.target, self.context, oid)

    async def _async_walk(self, base_oid: str) -> list[tuple[str, Any]]:
        await self._ensure_engine()
        await self._ensure_target()
        return await _do_next_walk(self.engine, self.auth_data, self.target, self.context, base_oid)

    async def _async_walk_many(self, *base_oids: str) -> list[list[tuple[str, Any]]]:
        """Walk several columns of one table together, one GETBULK stream for all.

        Returns one result list per base, in argument order - a drop-in for
        ``asyncio.gather(self._async_walk(a), self._async_walk(b), ...)``,
        which ran a separate stream per column.
        """
        await self._ensure_engine()
        await self._ensure_target()
        got = await _do_bulk_walk(self.engine, self.auth_data, self.target, self.context, list(base_oids))
        return [got[b] for b in base_oids]




    async def async_refresh_all(self) -> None:
        await self._ensure_engine()
        await self._ensure_target()
        await poll_interfaces(self, dynamic_only=False)
        await poll_ipv4(self)

    async def async_refresh_dynamic(self) -> None:
        await self._ensure_engine()
        await self._ensure_target()
        if not self.cache.get("ifTable"):
            await poll_interfaces(self, dynamic_only=False)
        else:
            await poll_interfaces(self, dynamic_only=True)
        # IPv4 data rarely changes; throttle separately (default 300 s).
        now_mono = time.monotonic()
        if (
            self._last_ipv4_poll == 0.0
            or (now_mono - self._last_ipv4_poll) >= self._ipv4_poll_interval
        ):
            self._last_ipv4_poll = now_mono
            await poll_ipv4(self)

    # ---------- coordinator hook ----------
    async def async_poll(self) -> Dict[str, Any]:
        await self._ensure_engine()
        await self._ensure_target()

        if self._cfgsave_busy:
            # A configuration save is running on the switch: only read the
            # save status (served without touching the switch database).
            await self._async_poll_cfgsave()
            return self.cache

        self._heavy_idle.clear()
        try:
            return await self._async_poll_full()
        finally:
            self._heavy_idle.set()

    async def _async_poll_full(self) -> Dict[str, Any]:
        # Keep system/diagnostic fields fresh (e.g., sysUpTime) so diagnostic
        # sensors update without requiring an integration restart.

        # sysUpTime can be very "chatty"; poll it less frequently.
        now_mono = time.monotonic()
        poll_uptime = (
            "sysUpTime" not in self.cache
            or (now_mono - self._last_uptime_poll) >= float(self._uptime_poll_interval)
        )

        if poll_uptime:
            self._last_uptime_poll = now_mono

        sysname_oid = self._custom_oid("name") or self._custom_oid("hostname") or OID_sysName
        uptime_oid = self._custom_oid("uptime") or OID_sysUpTime
        syscontact_oid = self._custom_oid("contact") or OID_sysContact
        syslocation_oid = self._custom_oid("location") or OID_sysLocation

        # Description, name, contact and location change when someone edits
        # them, not every ten seconds. They were re-read on every poll; now
        # they ride along with uptime (default every 5 min), in ONE request
        # rather than five. Edits made through this integration update the
        # cache directly (set_system_string), so they still show at once.
        static_known = all(k in self.cache for k in ("sysDescr", "sysName", "sysContact", "sysLocation"))
        sysdescr = sysname = sysuptime = syscontact = syslocation = None
        if poll_uptime or not static_known:
            got = await _do_get_many(
                self.engine, self.auth_data, self.target, self.context,
                [OID_sysDescr, sysname_oid, uptime_oid, syscontact_oid, syslocation_oid],
            )
            sysdescr, sysname, sysuptime = got.get(OID_sysDescr), got.get(sysname_oid), got.get(uptime_oid)
            syscontact, syslocation = got.get(syscontact_oid), got.get(syslocation_oid)
        if (not poll_uptime) and ("sysUpTime" in self.cache):
            sysuptime = self.cache.get("sysUpTime")
        if sysdescr is not None:
            self.cache["sysDescr"] = sysdescr
        if sysname is not None:
            self.cache["sysName"] = sysname
        if sysuptime is not None:
            self.cache["sysUpTime"] = sysuptime
        if syscontact is not None:
            self.cache["sysContact"] = syscontact
        if syslocation is not None:
            self.cache["sysLocation"] = syslocation

        # Populate manufacturer, firmware, model, and vendor flags dynamically on first poll
        if not self.cache.get("manufacturer"):
            await initialize_device_info(self)
        else:
            # Re-evaluate manufacturer/firmware from sysDescr on each subsequent poll
            await refresh_device_info(self)

        # RLTech: detect the save-configuration object once, then keep its
        # status / last-save time fresh (3 varbinds, cheap on the switch).
        if not self._cfgsave_probed:
            await self._async_probe_cfgsave()
        if self.cache.get("cfgsave_supported"):
            await self._async_poll_cfgsave()

        await self.async_refresh_dynamic()

        # Bandwidth counters (optional; per-device)
        await poll_bandwidth(self)

        # PoE (optional)
        try:
            await poll_poe(self)
        except Exception as e:
            _LOGGER.debug("PoE polling failed: %s", e)


        # Environmental power (Dell N-Series via private MIB)
        env_enabled = bool(self._env_options.get(CONF_ENV_ENABLE, False))
        env_mode = self._env_options.get(CONF_ENV_MODE, ENV_MODE_ATTRIBUTES)
        self.cache["env_enabled"] = env_enabled
        self.cache["env_mode"] = env_mode
        self.cache.setdefault("env_power_mw", {})
        self.cache.setdefault("env_power_mw_total", 0.0)

        if not env_enabled:
            # Keep keys stable, but clear values when disabled
            self.cache["env_power_mw"] = {}
            self.cache["env_power_mw_total"] = 0.0
        else:
            try:
                interval = int(self._env_options.get(CONF_ENV_POLL_INTERVAL, DEFAULT_ENV_POLL_INTERVAL))
            except Exception:
                interval = DEFAULT_ENV_POLL_INTERVAL

            should_poll = (now_mono - float(self._env_last_poll or 0.0)) >= interval
            # Always poll once on startup so we have initial values.
            should_poll = should_poll or float(self._env_last_poll or 0.0) == 0.0

            if should_poll:
                self._env_last_poll = now_mono
                try:
                    if self._get_vendor_info().get("environment_driver") == "h3c":
                        await poll_h3c_environment(self)
                    else:
                        vendor = self.cache.get("vendor", "Unknown")

                        # Memory
                        await poll_memory(self, vendor)

                        # CPU
                        await poll_cpu(self, vendor)

                        # Power
                        await poll_power(self, vendor)

                        # Fans
                        await poll_fans(self, vendor)

                        # PSU
                        await poll_psu(self, vendor)

                        # Temperature
                        await poll_temperature(self, vendor)

                        # Fallback
                        await poll_entity_sensor_fallback(self)
                except Exception as e:
                    _LOGGER.debug("Environmental features polling failed: %s", e)





        return self.cache

    # ---------- RLTech configuration save ----------
    @property
    def _cfgsave_busy(self) -> bool:
        return self._cfgsave_busy_until > 0 and time.monotonic() < self._cfgsave_busy_until

    async def _async_probe_cfgsave(self) -> None:
        """Set cache["cfgsave_supported"] for RLTech firmware that has rltMwCfgSaveStatus."""
        if not is_rltech_vendor(self.cache.get("vendor")):
            self._cfgsave_probed = True
            self.cache["cfgsave_supported"] = False
            return
        try:
            value = await self._async_get_one(OID_rltMwCfgSaveStatus)
        except (SnmpAuthError, SnmpConnectionError) as exc:
            # Transient: try again on the next poll.
            _LOGGER.debug("Save-configuration probe failed on %s: %s", self.host, exc)
            return
        if value is None:
            # errorStatus (e.g. genErr) instead of a value: not conclusive.
            _LOGGER.debug("Save-configuration probe on %s inconclusive, will retry", self.host)
            return
        self._cfgsave_probed = True
        status = _to_int(value)
        # A status 1..4 confirms support; any other value (older firmware
        # answers noSuchObject) confirms that the object does not exist.
        self.cache["cfgsave_supported"] = status in RLTECH_SAVE_STATUS
        _LOGGER.debug("Save-configuration support on %s: %s (status=%s)", self.host,
                      self.cache["cfgsave_supported"], value)

    def cfgsave_state(self) -> str:
        """"supported", "unsupported" (confirmed) or "unknown" (probe not conclusive yet)."""
        if self.cache.get("cfgsave_supported"):
            return "supported"
        if self._cfgsave_probed or not is_rltech_vendor(self.cache.get("vendor")):
            return "unsupported"
        return "unknown"

    async def _async_read_cfgsave(self) -> Optional[int]:
        """Read status / last-save time / sysUpTime in one GET; returns the status code."""
        got = await _do_get_many(
            self.engine, self.auth_data, self.target, self.context,
            [OID_rltMwCfgSaveStatus, OID_rltMwCfgSaveLastTime, OID_sysUpTime],
        )
        status = _to_int(got.get(OID_rltMwCfgSaveStatus))
        last = _to_int(got.get(OID_rltMwCfgSaveLastTime))
        uptime = _to_int(got.get(OID_sysUpTime))
        if status in RLTECH_SAVE_STATUS:
            self.cache["cfgsave_status"] = RLTECH_SAVE_STATUS[status]
        if last is not None and uptime is not None:
            self.cache["cfgsave_last_ticks"] = last
            self.cache["cfgsave_uptime_ticks"] = uptime
        return status

    async def _async_poll_cfgsave(self) -> None:
        try:
            status = await self._async_read_cfgsave()
        except (SnmpAuthError, SnmpConnectionError) as exc:
            _LOGGER.debug("Save-configuration status read failed on %s: %s", self.host, exc)
            return
        # save_config(wait=False) leaves the busy window open; close it as
        # soon as the switch no longer reports inProgress.
        if (
            self._cfgsave_busy_until
            and not self._cfgsave_lock.locked()
            and status in RLTECH_SAVE_STATUS
            and RLTECH_SAVE_STATUS[status] != "in_progress"
        ):
            self._cfgsave_busy_until = 0.0

    async def save_config(self, wait: bool = True, timeout: float = 40.0) -> str:
        """Write the running configuration of an RLTech switch to flash.

        Returns "success" (wait=True) or "accepted" (wait=False); raises
        HomeAssistantError with a readable reason otherwise.
        """
        if not self.cache.get("cfgsave_supported"):
            raise HomeAssistantError("This switch does not support saving the configuration via SNMP")
        if self._cfgsave_lock.locked():
            raise HomeAssistantError("A configuration save is already in progress for this switch")

        async with self._cfgsave_lock:
            await self._ensure_engine()
            await self._ensure_target()
            deadline = time.monotonic() + float(timeout)
            self._cfgsave_busy_until = deadline
            try:
                # Do not start while a heavy poll is still talking to the switch.
                try:
                    await asyncio.wait_for(self._heavy_idle.wait(), _CFGSAVE_WAIT_POLL_IDLE)
                except asyncio.TimeoutError:
                    _LOGGER.debug("Heavy poll still running on %s, saving anyway", self.host)

                # Status before the SET tells a retransmitted SET of our own
                # (agent already inProgress because of us) from a save that
                # was already running before we asked (another manager).
                try:
                    before = await self._async_read_cfgsave()
                except (SnmpAuthError, SnmpConnectionError):
                    before = None
                if RLTECH_SAVE_STATUS.get(before or 0) == "in_progress":
                    raise HomeAssistantError("The switch is already saving its configuration")

                try:
                    ok, err = await _do_set_integer(
                        self.engine, self.write_auth_data, self.target, self.context,
                        OID_rltMwCfgSaveAction, RLTECH_SAVE_ACTION_SAVE,
                    )
                except SnmpAuthError as exc:
                    raise HomeAssistantError(f"SNMP authentication failed while saving: {exc}") from exc
                except SnmpConnectionError as exc:
                    raise HomeAssistantError(
                        "The switch did not answer the save request. Check that a write "
                        f"community is configured and correct ({exc})"
                    ) from exc
                if not ok and "inconsistentvalue" in (err or "").lower():
                    # pysnmp retransmits a SET whose response was lost; the
                    # agent then answers the retransmission with
                    # inconsistentValue because OUR save is running. The
                    # save was not running before the SET (checked above),
                    # so inProgress now means our request was accepted.
                    try:
                        after = await self._async_read_cfgsave()
                    except (SnmpAuthError, SnmpConnectionError):
                        after = None
                    if RLTECH_SAVE_STATUS.get(after or 0) == "in_progress":
                        _LOGGER.debug("Save on %s: inconsistentValue after retransmission, save is running", self.host)
                        ok, err = True, None
                if not ok:
                    e = (err or "").lower()
                    if "inconsistentvalue" in e:
                        raise HomeAssistantError("The switch is already saving its configuration")
                    if "resourceunavailable" in e:
                        raise HomeAssistantError(
                            "Configuration saved too often; wait at least 10 seconds between "
                            "saves (at most 30 per hour) and try again"
                        )
                    if any(k in e for k in ("noaccess", "notwritable", "authorization", "nosuchname", "readonly")):
                        raise HomeAssistantError(
                            f"The switch refused the save request ({err}); check the write community"
                        )
                    raise HomeAssistantError(f"The switch refused the save request ({err})")

                self.cache["cfgsave_status"] = "in_progress"
                if not wait:
                    # Busy window stays open until a poll sees the save finish.
                    return "accepted"

                while time.monotonic() < deadline:
                    await asyncio.sleep(_CFGSAVE_STATUS_INTERVAL)
                    try:
                        status = await self._async_read_cfgsave()
                    except (SnmpAuthError, SnmpConnectionError) as exc:
                        _LOGGER.debug("Save status read failed on %s: %s", self.host, exc)
                        continue
                    name = RLTECH_SAVE_STATUS.get(status or 0)
                    if name == "success":
                        return "success"
                    if name == "failed":
                        raise HomeAssistantError("The switch reported that saving the configuration failed")
                raise HomeAssistantError(
                    f"The switch did not confirm the configuration save within {int(timeout)} s"
                )
            except BaseException:
                self._cfgsave_busy_until = 0.0
                raise
            finally:
                if wait:
                    self._cfgsave_busy_until = 0.0

    # ---------- mutations ----------
    async def set_alias(self, if_index: int, alias: str) -> bool:
        await self._ensure_engine()
        await self._ensure_target()
        ok = await _do_set_alias(self.engine, self.write_auth_data, self.target, self.context, if_index, alias)
        if ok:
            self.cache.setdefault("ifTable", {}).setdefault(if_index, {})["alias"] = alias
        else:
            _LOGGER.warning("Failed to set alias via SNMP on ifIndex %s", if_index)
        return ok

    async def set_admin_status(self, if_index: int, value: int) -> bool:
        await self._ensure_engine()
        await self._ensure_target()
        return await _do_set_admin_status(self.engine, self.write_auth_data, self.target, self.context, if_index, value)

    async def set_poe_admin(self, group_index: int, port_index: int, value: int) -> bool:
        await self._ensure_engine()
        await self._ensure_target()
        vendor = self.cache.get("vendor", "Unknown")
        poe_items = self._get_database_oids("poe", vendor)
        standard_item = None
        for item in poe_items:
            if "poe" in self.feature_overrides and item == self.feature_overrides["poe"]:
                standard_item = item
                break
            elif "oid_budget" in item:
                standard_item = item
                break
        custom_oid = (standard_item or {}).get("oid_port_admin")
        ok = await _do_set_poe_admin(self.engine, self.write_auth_data, self.target, self.context, group_index, port_index, value, custom_oid)
        if ok:
            # Update cache immediately
            ifindex_map = self.cache.get("ifindex_by_baseport", {})
            target_idx = ifindex_map.get(port_index, port_index)
            if target_idx in self.cache.setdefault("poe_ports", {}):
                self.cache["poe_ports"][target_idx]["admin"] = value
        else:
            _LOGGER.warning("Failed to set PoE admin via SNMP on group %s port %s", group_index, port_index)
        return ok

    async def set_poe_priority(self, group_index: int, port_index: int, value: int) -> bool:
        await self._ensure_engine()
        await self._ensure_target()
        vendor = self.cache.get("vendor", "Unknown")
        poe_items = self._get_database_oids("poe", vendor)
        standard_item = None
        for item in poe_items:
            if "poe" in self.feature_overrides and item == self.feature_overrides["poe"]:
                standard_item = item
                break
            elif "oid_budget" in item:
                standard_item = item
                break
        custom_oid = (standard_item or {}).get("oid_port_priority")
        ok = await _do_set_poe_priority(self.engine, self.write_auth_data, self.target, self.context, group_index, port_index, value, custom_oid)
        if ok:
            # Update cache immediately
            ifindex_map = self.cache.get("ifindex_by_baseport", {})
            target_idx = ifindex_map.get(port_index, port_index)
            if target_idx in self.cache.setdefault("poe_ports", {}):
                self.cache["poe_ports"][target_idx]["priority"] = value
        else:
            _LOGGER.warning("Failed to set PoE priority via SNMP on group %s port %s", group_index, port_index)
        return ok

    async def set_system_string(self, oid: str, value: str) -> bool:
        await self._ensure_engine()
        await self._ensure_target()
        ok = await _do_set_system_string(self.engine, self.write_auth_data, self.target, self.context, oid, value)
        if ok:
            if oid in (OID_sysName, self._custom_oid("name"), self._custom_oid("hostname")):
                self.cache["sysName"] = value
            elif oid in (OID_sysContact, self._custom_oid("contact")):
                self.cache["sysContact"] = value
            elif oid in (OID_sysLocation, self._custom_oid("location")):
                self.cache["sysLocation"] = value
        else:
            _LOGGER.warning("Failed to set system OID %s to value %s", oid, value)
        return ok
