# freebsd-pkg-repo

Binary FreeBSD packages for **riscv64**, an architecture the official
FreeBSD package mirrors do not cover at all.

`https://pkg.freebsd.org/` carries FreeBSD 13, 14, 15 and 16 for amd64,
aarch64, i386, armv6, armv7, powerpc, powerpc64 and powerpc64le. There is
no riscv64 in any version. So a FreeBSD riscv64 machine cannot install a
single binary package, which is why the riscv64 images in
[anyvm-org/freebsd-builder](https://github.com/anyvm-org/freebsd-builder)
ship without `rsync` or `sshfs`.

This repository builds the missing packages with poudriere and publishes
them as GitHub Release assets.

## Status

**Pilot in progress. There is nothing to install yet.** The design and the
implementation plan are committed; the first build has not produced a
usable repository. This README will carry the consumer configuration, the
public key and the measured build numbers once it does.

## How it works

- A GitHub Actions job starts a FreeBSD **amd64** VM via
  [vmactions/freebsd-vm](https://github.com/vmactions/freebsd-vm), which
  runs KVM-accelerated on the runner.
- Inside it, `qemu-riscv64-static` is registered with `binmiscctl(8)` and
  poudriere cross-builds into a **riscv64** jail created from the official
  release sets. Emulating only the riscv64 user-mode instructions on a
  fast amd64 host is far cheaper than emulating a whole riscv64 machine.
- Packages are renamed to GitHub-safe asset names and uploaded to numbered
  shard releases. GitHub allows at most 1000 assets per release, so a full
  package set needs about 40 shards.
- The index release holds `meta.conf`, `packagesite.pkg`, `data.pkg` and
  `ledger.json`. Every `repopath` in the manifest is rewritten to
  `../<shard-tag>/<asset>`, which pkg pastes onto the repository URL
  verbatim and GitHub's server resolves back onto the shard release.
- `ledger.json` records the state of every port, so a build that runs out
  of the 6-hour job limit simply resumes in the next job.

## Layout

| Path | What it does |
| --- | --- |
| `config/abis` | One row per ABI: slug, FreeBSD version, poudriere arch, jail name. |
| `config/pkglist` | Port origins to build. |
| `scripts/sanitize.py` | Package filename to GitHub-safe asset name. |
| `scripts/shard.py` | Permanent assignment of assets to shard releases. |
| `scripts/ledger.py` | Build ledger: schema, merge, pending/done queries. |
| `scripts/repoindex.py` | Rewrites manifest repopaths onto the shards. |
| `scripts/vm_build.sh` | Runs inside the VM: emulation, jail, poudriere. |
| `tests/` | Unit tests for the four pure modules. |

## Development

```
python3 -m unittest discover -s tests -v
```

Every tracked file must be 7-bit ASCII; CI enforces it.

Design and plan live in the
[anyvm-org](https://github.com/anyvm-org) tree under
`docs/superpowers/specs/` and `docs/superpowers/plans/`.

## Caveats

These packages are unofficial and community-built. They are not produced,
reviewed or endorsed by the FreeBSD Project.
