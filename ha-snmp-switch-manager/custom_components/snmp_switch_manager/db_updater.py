import logging
import os
import json
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval, async_call_later
from homeassistant.config_entries import ConfigEntry
from .const import DOMAIN, DB_SOURCE_REPO, DB_SOURCE_BRANCH, DB_SOURCE_PATH
from .database_builtin import merge_builtin

_LOGGER = logging.getLogger(__name__)

DEFAULT_DB_FILES = [
    "arp.json",
    "base_mac.json",
    "cpu.json",
    "device_info.json",
    "fans.json",
    "fdb.json",
    "interface_classification.json",
    "interface_filters.json",
    "lldp.json",
    "memory.json",
    "poe.json",
    "power.json",
    "psu.json",
    "rename_rules.json",
    "temperature.json",
    "vendors.json",
]


def get_database_files(db_path: str | None = None) -> list[str]:
    """Return all JSON database files found in the database directory, merged with known defaults."""
    if db_path is None:
        db_path = os.path.join(os.path.dirname(__file__), "database")
    discovered: set[str] = set()
    if os.path.exists(db_path):
        try:
            discovered = {f for f in os.listdir(db_path) if f.endswith(".json")}
        except Exception as e:
            _LOGGER.warning("Could not scan database directory %s: %s", db_path, e)
    return sorted(discovered | set(DEFAULT_DB_FILES))


DB_FILES = list(DEFAULT_DB_FILES)

def build_raw_url_root(
    repo: str = DB_SOURCE_REPO,
    branch: str = DB_SOURCE_BRANCH,
    path: str = DB_SOURCE_PATH,
) -> str:
    """Return the raw.githubusercontent.com URL prefix of the database directory."""
    repo = str(repo or DB_SOURCE_REPO).strip().strip("/")
    branch = str(branch or DB_SOURCE_BRANCH).strip().strip("/")
    path = str(path or DB_SOURCE_PATH).strip().strip("/")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}/"


# Database source (configurable in const.py: DB_SOURCE_REPO / DB_SOURCE_BRANCH)
RAW_URL_ROOT = build_raw_url_root()


async def async_check_and_update_db(hass: HomeAssistant) -> bool:
    """Download updated database files from GitHub and save them if changed."""
    session = async_get_clientsession(hass)
    db_path = os.path.join(os.path.dirname(__file__), "database")
    timeout = aiohttp.ClientTimeout(total=15, connect=5)
    
    updated_any = False
    
    filenames = await hass.async_add_executor_job(get_database_files, db_path)
    for filename in filenames:
        url = f"{RAW_URL_ROOT}{filename}"
        local_path = os.path.join(db_path, filename)
        
        try:
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    _LOGGER.debug("Skipping update check for %s: HTTP %s", filename, response.status)
                    continue
                
                content = await response.text()
                # Parse to ensure it is valid JSON
                new_data = json.loads(content)
                # Keep built-in vendor entries (e.g. RLTech) in the stored file.
                # Comparing the merged data also avoids a reload loop when the
                # upstream file simply lacks the built-in entries.
                new_data = merge_builtin(filename[:-5], new_data)
                new_str = json.dumps(new_data, indent=2, sort_keys=True)
                
                # Load current local file
                def read_local() -> str | None:
                    if not os.path.exists(local_path):
                        return None
                    try:
                        with open(local_path, "r", encoding="utf-8") as f:
                            return json.dumps(json.load(f), indent=2, sort_keys=True)
                    except Exception:
                        return None
                old_str = await hass.async_add_executor_job(read_local)
                
                if old_str != new_str:
                    _LOGGER.info("Updating local database file: %s", filename)
                    
                    def write_local(path: str, data_str: str) -> None:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(data_str)
                            
                    await hass.async_add_executor_job(
                        write_local, 
                        local_path, 
                        json.dumps(new_data, indent=2)
                    )
                    updated_any = True
                    
        except aiohttp.ClientConnectorError as e:
            _LOGGER.warning("Cannot connect to GitHub for database updates: %s", e)
            break
        except (TimeoutError, aiohttp.ClientError) as e:
            err_msg = str(e) or type(e).__name__
            _LOGGER.warning("Could not check database update for %s (%s)", filename, err_msg)
        except Exception as e:
            err_msg = str(e) or type(e).__name__
            _LOGGER.warning("Failed to check database update for %s: %s", filename, err_msg)
            
    return updated_any


async def async_setup_db_updater(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up the periodic background database updater."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
        
    # Store entry reference
    entries = hass.data[DOMAIN].setdefault("entries", {})
    entries[entry.entry_id] = entry
    
    if "db_updater_timer" in hass.data[DOMAIN]:
        return
        
    async def run_update(now: Any = None) -> None:
        _LOGGER.debug("Starting background database update check...")
        updated = await async_check_and_update_db(hass)
        if updated:
            _LOGGER.info("Database updated! Reloading all active SNMP Switch Manager config entries...")
            for active_entry in list(entries.values()):
                hass.async_create_task(
                    hass.config_entries.async_reload(active_entry.entry_id)
                )

    # Schedule periodic checks every 6 hours
    hass.data[DOMAIN]["db_updater_timer"] = async_track_time_interval(
        hass,
        run_update,
        timedelta(hours=6)
    )
    
    # Run immediate check shortly after start to avoid blocking initialization
    def deferred_start(_: Any) -> None:
        hass.add_job(run_update())
        
    async_call_later(hass, 60, deferred_start)


def async_unload_db_updater(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up and remove background updater if no entries remain."""
    if DOMAIN not in hass.data:
        return
        
    entries = hass.data[DOMAIN].get("entries", {})
    entries.pop(entry.entry_id, None)
    
    if not entries:
        timer = hass.data[DOMAIN].pop("db_updater_timer", None)
        if timer:
            timer()
            _LOGGER.debug("Cancelled background database updater timer")
