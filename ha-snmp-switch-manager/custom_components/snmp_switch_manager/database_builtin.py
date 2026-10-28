"""Built-in database entries that must survive automatic database updates.

The JSON files in ``database/`` are periodically replaced by the copies
published in the configured source repository (see ``db_updater.py``). Vendor
support that is not (yet) part of that repository would be lost on the next
update, so the entries below are merged on top of every database file:

* when the files are loaded (``SwitchSnmpClient._load_database`` and
  ``config_flow.load_database``), and
* before a downloaded file is compared with / written to disk, so the local
  file keeps the built-in entries and an unchanged upstream file does not
  trigger a needless reload.

Merging is idempotent and never overrides an entry that already exists
upstream (matched by vendor name, case-insensitive). Entries written under
a legacy vendor name (LEGACY_VENDOR_ALIASES) are renamed first.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

RLTECH_VENDOR = "RLTech"

# Vendor names used by earlier development builds of this integration for
# the same firmware (lower-case -> current name). Database files written by
# those builds, or kept in a forked database repository, are rewritten on
# load (see _migrate_legacy_*). Only entries that are unambiguously ours are
# touched (see the sysObjectID check in _migrate_legacy_vendors).
LEGACY_VENDOR_ALIASES: Dict[str, str] = {
    "airoha": RLTECH_VENDOR,  # pre-release name of the RLTech entry
}

_RLTECH_ENTERPRISE_PREFIX = "1.3.6.1.4.1.62570"


def is_rltech_vendor(name: Any) -> bool:
    """True for the RLTech vendor name, including its legacy alias (case-insensitive)."""
    n = str(name or "").strip().lower()
    return n == RLTECH_VENDOR.lower() or LEGACY_VENDOR_ALIASES.get(n) == RLTECH_VENDOR


# RLTech managed switches (MW firmware, EN8851 / AN8858).
# sysObjectID is 1.3.6.1.4.1.62570.<chip>.<productId>; the chip keywords in
# sysDescr ("EN8851, MW v26.06, ...") remain as a fallback, e.g. for firmware
# built with a different enterprise number.
BUILTIN_VENDORS: List[Dict[str, Any]] = [
    {
        "name": RLTECH_VENDOR,
        "enterprise_number": "62570",
        "sys_object_id_prefix": _RLTECH_ENTERPRISE_PREFIX,
        "keywords": ["en8851", "an8858"],
        "manufacturer_fallback": "RLTech",
    },
]

# ENTITY-MIB chassis row (entPhysicalIndex 1) served by the MW firmware.
BUILTIN_DEVICE_INFO: List[Dict[str, Any]] = [
    {
        "oid_firmware": "1.3.6.1.2.1.47.1.1.1.1.10.1",
        "oid_model": "1.3.6.1.2.1.47.1.1.1.1.13.1",
        "oid_mfg": "1.3.6.1.2.1.47.1.1.1.1.12.1",
        "vendors": [RLTECH_VENDOR],
        "description": "RLTech MW ENTITY-MIB chassis row",
    },
]

# RLTech MW firmware stores sysName / ifAlias in its JSON startup-config
# without escaping; it rejects (wrongValue) anything but printable ASCII
# excluding '"', '\\', '{', '}', and limits both strings to 32 bytes.
RLTECH_STRING_MAX_LEN = 32
RLTECH_STRING_FORBIDDEN = '"\\{}'


def rltech_string_error(value: str, max_len: int = RLTECH_STRING_MAX_LEN) -> str | None:
    """Return a human readable reason why ``value`` would be rejected by an RLTech switch, else None."""
    text = str(value if value is not None else "")
    bad = sorted({ch for ch in text if not (0x20 <= ord(ch) <= 0x7E) or ch in RLTECH_STRING_FORBIDDEN})
    if bad:
        shown = " ".join(repr(ch) for ch in bad[:5])
        return (
            "RLTech switches only accept printable ASCII characters except "
            '\" \\ { } (invalid: ' + shown + ")"
        )
    if len(text.encode("ascii")) > max_len:
        return f"RLTech switches accept at most {max_len} characters (got {len(text)})"
    return None


# Database features whose "Standard" entry also applies to RLTech:
# the vendor name is appended to that entry's "vendors" list.
BUILTIN_STANDARD_MEMBERSHIP: Dict[str, List[str]] = {
    "base_mac": [RLTECH_VENDOR],
    "fdb": [RLTECH_VENDOR],
    "lldp": [RLTECH_VENDOR],
}


def _legacy_name(name: Any) -> str | None:
    """Current vendor name for a legacy alias, else None."""
    return LEGACY_VENDOR_ALIASES.get(str(name or "").strip().lower())


# Keyword sets that identify a legacy entry written for this firmware when it
# carries no sysObjectID prefix (development builds before the PEN was set:
# prefix "", keywords ["airoha", "en8851", "an8858"]).
_LEGACY_KEYWORD_MARKERS = ({"airoha"}, {"en8851", "an8858"})


def _is_own_legacy_vendor(v: Any) -> bool:
    """True for a vendor entry under a legacy name that was written for this firmware.

    Matches the RLTech enterprise prefix, or an empty prefix together with the
    keywords used by the development builds. An unrelated vendor that merely
    shares the legacy name (own, different prefix) is not matched.
    """
    if not isinstance(v, dict) or not _legacy_name(v.get("name")):
        return False
    prefix = str(v.get("sys_object_id_prefix") or "").strip().lstrip(".")
    if prefix == _RLTECH_ENTERPRISE_PREFIX:
        return True
    if prefix:
        return False
    keywords = {str(k).strip().lower() for k in (v.get("keywords") or []) if str(k).strip()}
    return any(marker <= keywords for marker in _LEGACY_KEYWORD_MARKERS)


def _migrate_legacy_vendors(data: Dict[str, Any]) -> None:
    """Drop vendor entries that are a legacy alias of a built-in one (see _is_own_legacy_vendor)."""
    vendors = data.get("vendors")
    if not isinstance(vendors, list):
        return
    data["vendors"] = [v for v in vendors if not _is_own_legacy_vendor(v)]


def _migrate_legacy_feature(data: Dict[str, Any], key: str, builtin_items: List[Dict[str, Any]]) -> None:
    """Rename legacy vendor names in feature ``vendors`` lists (deduplicated).

    An item that, after renaming, is identical to a built-in item apart from
    its description is removed; the built-in copy is merged back afterwards.
    """
    items = data.get(key)
    if not isinstance(items, list):
        return
    out = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        vlist = item.get("vendors")
        if isinstance(vlist, list) and any(_legacy_name(v) for v in vlist):
            renamed: List[Any] = []
            for v in vlist:
                nv = _legacy_name(v) or v
                if not any(str(x).lower() == str(nv).lower() for x in renamed):
                    renamed.append(nv)
            item = dict(item)
            item["vendors"] = renamed
            strip = {k: v for k, v in item.items() if k != "description"}
            if any(strip == {k: v for k, v in b.items() if k != "description"} for b in builtin_items):
                continue
        out.append(item)
    data[key] = out


def _has_vendor(items: List[Dict[str, Any]], vendor: str) -> bool:
    vendor_l = vendor.lower()
    for item in items:
        for v in item.get("vendors", []) or []:
            if str(v).lower() == vendor_l:
                return True
    return False


def _merge_vendors(data: Dict[str, Any]) -> None:
    vendors = data.setdefault("vendors", [])
    if not isinstance(vendors, list):
        return
    names = {str(v.get("name", "")).lower() for v in vendors if isinstance(v, dict)}
    for entry in BUILTIN_VENDORS:
        if entry["name"].lower() in names:
            continue
        # Keep "Standard" as the generic catch-all: insert right before it.
        pos = len(vendors)
        for i, v in enumerate(vendors):
            if isinstance(v, dict) and v.get("name") == "Standard":
                pos = i
                break
        vendors.insert(pos, copy.deepcopy(entry))
        names.add(entry["name"].lower())


def _merge_feature_items(data: Dict[str, Any], key: str, builtin_items: List[Dict[str, Any]]) -> None:
    items = data.setdefault(key, [])
    if not isinstance(items, list):
        return
    for entry in builtin_items:
        missing = [v for v in entry.get("vendors", []) if not _has_vendor(items, v)]
        if missing:
            items.append(copy.deepcopy(entry))


def _merge_standard_membership(data: Dict[str, Any], key: str, vendors_to_add: List[str]) -> None:
    items = data.get(key)
    if not isinstance(items, list):
        return
    for vendor in vendors_to_add:
        if _has_vendor(items, vendor):
            continue
        for item in items:
            vlist = item.get("vendors")
            if isinstance(vlist, list) and "Standard" in vlist:
                vlist.append(vendor)
                break


def merge_builtin(key: str, data: Any) -> Any:
    """Return a copy of database ``data`` (file stem ``key``) with built-in entries merged in."""
    if not isinstance(data, dict):
        return data
    merged = copy.deepcopy(data)
    if key == "vendors":
        _migrate_legacy_vendors(merged)
        _merge_vendors(merged)
    elif key == "device_info":
        _migrate_legacy_feature(merged, "device_info", BUILTIN_DEVICE_INFO)
        _merge_feature_items(merged, "device_info", BUILTIN_DEVICE_INFO)
    elif key in BUILTIN_STANDARD_MEMBERSHIP:
        _migrate_legacy_feature(merged, key, [])
    if key in BUILTIN_STANDARD_MEMBERSHIP:
        _merge_standard_membership(merged, key, BUILTIN_STANDARD_MEMBERSHIP[key])
    return merged


def merge_builtin_all(database: Dict[str, Any]) -> Dict[str, Any]:
    """Merge built-in entries into a loaded database dict ({file stem: data})."""
    out = dict(database or {})
    for key in ("vendors", "device_info", *BUILTIN_STANDARD_MEMBERSHIP.keys()):
        if key in out:
            out[key] = merge_builtin(key, out[key])
        elif key == "vendors":
            out[key] = merge_builtin(key, {"vendors": []})
        elif key == "device_info":
            out[key] = merge_builtin(key, {"device_info": []})
    return out
