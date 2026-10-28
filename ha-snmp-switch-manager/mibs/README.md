# RLTech MIB files

| File | Module | Root OID |
|---|---|---|
| `RLTECH-SMI.mib` | `RLTECH-SMI` – enterprise tree, product identifiers | `1.3.6.1.4.1.62570` |
| `RLTECH-MW-MIB.mib` | `RLTECH-MW-MIB` – managed switch firmware objects | `1.3.6.1.4.1.62570.100.1` |

`RLTECH-MW-MIB` imports `RLTECH-SMI`, `SNMPv2-SMI`, `SNMPv2-TC` and `SNMPv2-CONF`
(the SNMPv2 modules ship with net-snmp / most network management tools).
Home Assistant itself does not need these files.

## Loading

net-snmp:

```bash
# one-off
snmpget -v2c -c public -M +/path/to/mibs -m +RLTECH-MW-MIB <switch-ip> \
    RLTECH-MW-MIB::rltMwCfgSaveStatus.0
snmptranslate -M +/path/to/mibs -m +RLTECH-MW-MIB -On RLTECH-MW-MIB::rltMwCfgSaveAction.0
#   .1.3.6.1.4.1.62570.100.1.1.1.1.1.0

# permanently: copy both files to ~/.snmp/mibs/ and add to ~/.snmp/snmp.conf
#   mibs +RLTECH-SMI:RLTECH-MW-MIB
```

Other tools (Zabbix, LibreNMS, PRTG, MIB browsers): import `RLTECH-SMI.mib`
first, then `RLTECH-MW-MIB.mib`.

## OID quick reference

```
1.3.6.1.4.1.62570                  rltech                 RLTECH-SMI
1.3.6.1.4.1.62570.1                rltProdEN8851          sysObjectID = .1.<model number>
1.3.6.1.4.1.62570.2                rltProdAN8858          sysObjectID = .2.<model number>
1.3.6.1.4.1.62570.3                rltProdAN8855          reserved
1.3.6.1.4.1.62570.4 - .99          reserved (product identification only)
1.3.6.1.4.1.62570.100              rltMgmt                root of all RLTech MIB modules
1.3.6.1.4.1.62570.100.1            rltMwMIB               RLTECH-MW-MIB
1.3.6.1.4.1.62570.100.1.1.1.1.1.0  rltMwCfgSaveAction     RW  save(1) / noOp(2)
1.3.6.1.4.1.62570.100.1.1.1.1.2.0  rltMwCfgSaveStatus     RO  idle(1) inProgress(2) success(3) failed(4)
1.3.6.1.4.1.62570.100.1.1.1.1.3.0  rltMwCfgSaveLastTime   RO  TimeStamp (sysUpTime of the last successful save)
```

### Saving the configuration

SNMP SETs of standard objects (ifAdminStatus, ifAlias, sysName, ...) only
change the running configuration. To keep them across a reboot:

```bash
O=1.3.6.1.4.1.62570.100.1.1.1.1
snmpset -v2c -c <write-community> <switch-ip> $O.1.0 i 1   # accepted -> noError
snmpget -v2c -c <read-community>  <switch-ip> $O.2.0 $O.3.0 # inProgress(2), then success(3)
```

- The SET response only means "accepted"; poll `rltMwCfgSaveStatus`.
- `inconsistentValue`: a save is still running. `resourceUnavailable`: less
  than 10 s since the previous save, or 30 saves within the last 60 minutes.
- Send the save in its own PDU after the changes (or as the last varbind).
- Status and time only reflect saves started via SNMP.

## Revisions

| Module | REVISION | Notes |
|---|---|---|
| RLTECH-SMI | 202610220000Z | Initial version |
| RLTECH-MW-MIB | 202610220000Z | Initial version: configuration save objects |
