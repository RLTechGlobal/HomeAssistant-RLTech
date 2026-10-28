# RLTech Home Assistant integrations

Monorepo for the RLTech Home Assistant custom integrations.

Each integration lives in its own top-level directory and is a complete,
self-contained HACS repository: its own `hacs.json`, `custom_components/`,
tests, screenshots and README.

| Directory | Integration | Requires Home Assistant | HACS repository |
|---|---|---|---|
| [`ha-rltech-fttr/`](ha-rltech-fttr/README.md) | RLTech FTTR (AC/OLT + managed FTTR APs) | 2026.9.0 | [RLTechGlobal/ha-rltech-fttr](https://github.com/RLTechGlobal/ha-rltech-fttr) |
| [`ha-snmp-switch-manager/`](ha-snmp-switch-manager/README.md) | SNMP Switch Manager (fork, RLTech switches) | see its README | not published yet |

## Forked integrations

`ha-snmp-switch-manager/` is a fork of
[OtisPresley/snmp-switch-manager](https://github.com/OtisPresley/snmp-switch-manager)
(MIT License, Copyright (c) 2025 OtisPresley; the original `LICENSE` is kept in
that directory and applies to it).

| Item | Value |
|---|---|
| Upstream repository | https://github.com/OtisPresley/snmp-switch-manager |
| Upstream branch | `main` |
| Upstream commit forked | [`138a0d45ffeb8b0f4918404d1da743e5ce194b21`](https://github.com/OtisPresley/snmp-switch-manager/commit/138a0d45ffeb8b0f4918404d1da743e5ce194b21) (2026-10-24, version 0.6.10) |

The first commit that adds the directory is an unmodified copy of that upstream
commit, so `git diff <that commit> -- ha-snmp-switch-manager/` shows exactly what
RLTech changed. To pick up a newer upstream release, diff the upstream commits
since the one above and apply them to this directory.

It is not in the sync workflow matrix yet: the workflow copies the root
`LICENSE`, `LICENSE-MIT` and `NOTICE` into the published repository, which would
replace this fork's own MIT `LICENSE`, so that step needs adjusting first.

## Why the extra repository per integration

HACS only accepts a repository with `hacs.json` at its root and exactly one
integration under `custom_components/` at that same root, so a subdirectory of
this monorepo cannot be added to HACS directly.

Write code here; publish it from there. The
[Sync integration repositories](.github/workflows/sync-integrations.yml)
workflow mirrors each integration directory into its own repository on every
push to `main`, and tags the published repository with the version from that
integration's `manifest.json`.

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

## License

Licensed under the Apache License 2.0 ([LICENSE](LICENSE)); see
[NOTICE](NOTICE) and [LICENSE-MIT](LICENSE-MIT) for the MIT-licensed baseline
this work is based on. These three files are copied into each published
integration repository.

## About RLTech

RLTech builds all-optical FTTR (Fiber To The Room) networking for homes and SMB
sites: main/slave gateways, ONTs, ACs, APs and switches, with local management
options such as these Home Assistant integrations alongside cloud platforms.

- Website: [www.rltechnet.com](https://www.rltechnet.com)
- Product and network management cloud: [AVASA](https://avasa.net)
