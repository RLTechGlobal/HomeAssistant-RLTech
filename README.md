# RLTech Home Assistant integrations

Monorepo for the RLTech Home Assistant custom integrations.

Each integration lives in its own top-level `ha-*/` directory with its own
`hacs.json`, `custom_components/<domain>/` and README, so the directory can be
published as a HACS repository on its own.

| Directory | Integration | Requires Home Assistant | License | HACS repository |
|---|---|---|---|---|
| [`ha-rltech-fttr/`](ha-rltech-fttr/README.md) | RLTech FTTR (AC/OLT + managed FTTR APs) | 2026.9.0 | Apache-2.0 (see [License](#license)) | [RLTechGlobal/ha-rltech-fttr](https://github.com/RLTechGlobal/ha-rltech-fttr) |
| [`ha-snmp-switch-manager/`](ha-snmp-switch-manager/README.md) | SNMP Switch Manager, forked with RLTech switch support | 2025.11.2 | MIT ([its own `LICENSE`](ha-snmp-switch-manager/LICENSE)) | not published yet |

## Forked integration: `ha-snmp-switch-manager/`

A fork of
[OtisPresley/snmp-switch-manager](https://github.com/OtisPresley/snmp-switch-manager),
a generic SNMP switch integration, extended to manage RLTech switches.

### Fork point

| Item | Value |
|---|---|
| Upstream repository | https://github.com/OtisPresley/snmp-switch-manager |
| Upstream branch | `main` |
| Upstream commit | [`138a0d45ffeb8b0f4918404d1da743e5ce194b21`](https://github.com/OtisPresley/snmp-switch-manager/commit/138a0d45ffeb8b0f4918404d1da743e5ce194b21) (2026-10-24) |
| Upstream version | 0.6.10 |
| Import commit in this repository | `ad2adee` — an unmodified copy of the upstream commit |

Because the import commit is unmodified upstream code, this shows every RLTech
change:

```sh
git diff ad2adee HEAD -- ha-snmp-switch-manager/
```

To pick up a newer upstream release, apply the upstream changes since the fork
point to this directory, then update the table above.

### RLTech changes

- **RLTech switch detection:** switches are detected by the sysObjectID prefix
  `1.3.6.1.4.1.62570`. The `en8851` / `an8858` keywords in sysDescr are a
  fallback.
- **Built-in vendor entries:** the entries in `database_builtin.py` survive
  automatic database updates.
- **Migration:** earlier "Airoha" database entries and options are migrated to
  RLTech.
- **Write community:** an optional SNMP v2c write community is used for all SET
  requests. When it is empty, SET requests use the read community.
- **Save configuration:** a *Save Configuration* button and the
  `snmp_switch_manager.save_config` service write the running configuration to
  flash through RLTECH-MW-MIB `rltMwCfgSaveAction`. Two diagnostic sensors show
  the save status and the last save time.
- **Input validation:** the system name and port descriptions are checked
  against what the RLTech firmware accepts before the SET is sent.
- **sysObjectID matching:** prefixes are matched on OID arc boundaries, so
  `1.3.6.1.4.1.9` no longer matches `1.3.6.1.4.1.99…`.
- **Database updater source:** the source repository is configurable in
  `const.py`. The default is still upstream.
- **MIB files:**
  [`mibs/RLTECH-SMI.mib`](ha-snmp-switch-manager/mibs/RLTECH-SMI.mib) and
  [`mibs/RLTECH-MW-MIB.mib`](ha-snmp-switch-manager/mibs/RLTECH-MW-MIB.mib).

Details are in
[`ha-snmp-switch-manager/CHANGELOG.md`](ha-snmp-switch-manager/CHANGELOG.md)
under *Unreleased*.

### Before publishing it

Before it is published, the directory needs these changes:

1. **License:** the sync workflow copies the root `LICENSE`, `LICENSE-MIT` and
   `NOTICE` into the published repository, which would overwrite this fork's
   MIT `LICENSE`. The copy step must skip this directory, or the license
   notices must be combined.
2. **Links:** the documentation and issue tracker URLs in
   `custom_components/snmp_switch_manager/manifest.json` and the links in its
   README still point at upstream.
3. **Database updater:** the updater source in `const.py` still points at
   upstream.
4. **Workflows:** the upstream `.github/` workflows (CI, hassfest, HACS
   validation, PR automation) come with the directory. Review them, because
   they will run in the published repository.

## Why the extra repository per integration

HACS only accepts a repository with `hacs.json` at its root and exactly one
integration under `custom_components/` at that same root, so a subdirectory of
this monorepo cannot be added to HACS directly.

Write code here; publish it from there. The
[Sync integration repositories](.github/workflows/sync-integrations.yml)
workflow does the publishing on every push to `main`:

- It mirrors each integration directory listed in its matrix into that
  integration's own repository.
- It tags the published repository with the version from the integration's
  `manifest.json`.

| What | Where |
|---|---|
| Development, pull requests, history | this repository |
| HACS installs and updates | the per-integration repository |
| Issue reports from users | the per-integration repository (its README is what HACS renders) |

## Setting up publishing

1. Create the target repository (for example `RLTechGlobal/ha-rltech-fttr`)
   with a `main` branch — an initial commit is enough, the workflow replaces
   its contents.
2. Create a fine-grained personal access token that can push to that
   repository, and store it as the `HACS_SYNC_TOKEN` repository secret.
3. Add the directory/domain/repository pair to the `matrix.include` list of the
   workflow and push the change; the workflow then syncs on every push to
   `main`.

## Adding an integration

1. Create `ha-<name>/` with `hacs.json`, `custom_components/<domain>/`,
   `tests/`, `requirements.test*.txt`, `setup.cfg` and a `README.md` — the
   layout of `ha-rltech-fttr/` is the template.
2. Make every link inside that README and its `manifest.json` point at the
   published repository, not at this monorepo: after syncing, the directory
   *is* the repository root.
3. Add it to the workflow matrix and to the table above.

## Testing

Tests run from inside the integration directory (that directory is the
repository root once published):

```sh
cd ha-rltech-fttr
python3 -m venv .venv
.venv/bin/pip install -r requirements.test.txt
.venv/bin/python -m pytest
```

See [`ha-rltech-fttr/tests/README.md`](ha-rltech-fttr/tests/README.md) for the
Home Assistant runtime and frontend checks.

`ha-snmp-switch-manager/` has no Python test suite; upstream checks it with the
hassfest and HACS validation workflows under its `.github/workflows/`.

## License

The repository is licensed under the Apache License 2.0 ([LICENSE](LICENSE)).
This applies to everything except `ha-snmp-switch-manager/`.

- **`ha-rltech-fttr/`:** based on an MIT-licensed baseline, described in
  [NOTICE](NOTICE) and [LICENSE-MIT](LICENSE-MIT). The sync workflow copies
  `LICENSE`, `NOTICE` and `LICENSE-MIT` into its published repository.
- **`ha-snmp-switch-manager/`:** stays under its upstream MIT License,
  Copyright (c) 2025 OtisPresley. See
  [`ha-snmp-switch-manager/LICENSE`](ha-snmp-switch-manager/LICENSE).

## About RLTech

RLTech builds all-optical FTTR (Fiber To The Room) networking for homes and SMB
sites: main/slave gateways, ONTs, ACs, APs and switches, with local management
options such as these Home Assistant integrations alongside cloud platforms.

- Website: [www.rltechnet.com](https://www.rltechnet.com)
- Product and network management cloud: [AVASA](https://avasa.net)
