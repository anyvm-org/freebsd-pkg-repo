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

**Whole-tree build in progress since 2026-09-03.** Shards
`pkg-FreeBSD-15-riscv64-000` (full, 985 packages) and `-001` hold 986
packages after five parallel rounds (2026-09-04): the bootstrap set (`pkg`, `rsync`, `sshfs`,
`bash`, `sudo`, `curl`, `git`, `python3`, `tree` and their closure)
plus the base layer of the tree (perl, ruby, cmake, glib, icu,
binutils, gnutls, coreutils, gmp, ...). Everything is signed with the
ECDSA key below, and a real FreeBSD 15.1 riscv64 guest verifies the
signature with its shipped `pkg`, upgrades `pkg` from the shard and
installs `rsync` and `fusefs-sshfs` from it. About 26,600 ports remain
pending; each round of 18 five-hour jobs adds what it can, and ports
that do not fit a job are left for a bigger machine.

## Installing

On a FreeBSD 15.x riscv64 machine, as root:

```
mkdir -p /usr/local/etc/pkg/repos /usr/local/etc/pkg/keys
fetch -o /usr/local/etc/pkg/keys/anyvm.pub \
  https://github.com/anyvm-org/freebsd-pkg-repo/releases/download/idx-FreeBSD-15-riscv64/repo.pub
sha256 /usr/local/etc/pkg/keys/anyvm.pub
fetch -o /usr/local/etc/pkg/repos/anyvm.conf \
  https://github.com/anyvm-org/freebsd-pkg-repo/releases/download/idx-FreeBSD-15-riscv64/anyvm.conf
pkg update
pkg install tree
```

The `sha256` line must print the fingerprint in the next section.
`anyvm.conf` carries one repository block per shard release; fetch it
again when a new shard appears. The stock `FreeBSD` repository has no
riscv64 packages at all, so silencing it avoids noise:

```
printf 'FreeBSD: { enabled: no }\n' > /usr/local/etc/pkg/repos/FreeBSD.conf
```

The `pkg` the anyvm riscv64 image ships (2.6.2 on 15.1) verifies the
ECDSA signature as-is, then upgrades itself to the `pkg` in the shard
before installing anything else. A machine with no `pkg` at all cannot
use the `pkg` bootstrapper, which insists on a `Latest/pkg.pkg` path that
a flat release cannot serve; instead fetch `pkg-<version>.pkg` from the
shard release by name, extract `pkg-static` from it, and run
`pkg-static add` on it.

## Measured

Pilot slice, `sysutils/tree` (five packages: pkg, indexinfo,
gettext-runtime, gmake, tree):

| | local VM, 16 cores | GitHub runner, 4 cores |
| --- | --- | --- |
| whole build step | 27 min | 38m07s |
| `poudriere bulk`, 5 packages | 16m08s | 34m41s |
| `ports-mgmt/pkg` alone | not isolated | 15m57s |
| jail creation, `NO_SRC=yes` | 9m59s | not isolated |

Earlier local slice, `net/rsync` and its closure, 32 packages, 16 cores:
2h42m total, 304 s per package, 8.7 GB peak disk in the VM, zero
failures.

Consumer check: FreeBSD 15.1-RELEASE riscv64 under QEMU TCG, shipped
`pkg` 2.6.2, `pkg update` verified the signature, `pkg install tree`
fetched from the release and ran. No manual bootstrap was needed.

Bootstrap slice (`config/pkglist.bootstrap`, 115 ports queued), local
VM with 16 cores:

| | |
| --- | --- |
| first attempt | 48 built in 45m11s (68 s per package), then `lang/python312` failed in its package phase and 66 dependents were skipped |
| cause | Python's `compileall -jN` uses multiprocessing, whose semaphores misbehave under qemu-user; the port ignores the error and every `.pyc` in the plist goes missing |
| fix | `MAKE_JOBS_UNSAFE=yes` for `lang/python3*` only (`poudriere.d/make.conf`); python312 then built in 24m54s |
| `prepare` step | 569 s on a fresh jail (jail creation 9m01s); cached with the image afterwards |

On the 4-core runner the same slice, with the fix, builds about 92
packages per 4.5-hour `BUILD_DEADLINE` (176 s per package, `python312`
among them, zero failures). The first such run lost its packages
because they lived inside the VM; with the package directory on the
runner, the run of 2026-09-02 seeded the 5 published packages
(`Queued: 110`), built 92 more, was stopped by the watchdog, and
published all 97 to shard 000 in the same job; the next run seeds those
97 and builds the remaining 18. The `prepare` step took about two
minutes on the runner (jail creation 9 s from download.freebsd.org)
and the cached prepared image is 1.71 GiB.

## Signing key

Every shard release and the index release carry `repo.pub`, the public
half of the repository signing key. It is an ECDSA key created with
`pkg key --create -t ecdsa`, so it is a binary DER file, not PEM; pkg's
ECDSA/EdDSA signers accept only keys made by `pkg key`, not by OpenSSL.

SHA-256 of `repo.pub`:

```
a9e2f84083b916f0f9f2bda18ebf9cc581cfb28aaadb6827836f1ddc672a3040
```

Check a downloaded copy with `sha256 repo.pub` (FreeBSD) or
`sha256sum repo.pub` (Linux) before installing it as
`/usr/local/etc/pkg/keys/anyvm.pub`. A signed index that does not verify
against this key is rejected by pkg outright ("Invalid signature,
removing repository"), which is the intended failure mode.

## How it works

- A GitHub Actions job starts a FreeBSD **amd64** VM via
  [vmactions/freebsd-vm](https://github.com/vmactions/freebsd-vm), which
  runs KVM-accelerated on the runner.
- Inside it, `qemu-riscv64-static` is registered with `binmiscctl(8)` and
  poudriere cross-builds into a **riscv64** jail created from the official
  release sets. Emulating only the riscv64 user-mode instructions on a
  fast amd64 host is far cheaper than emulating a whole riscv64 machine.
- The jail, the ports tree and the poudriere configuration are made once
  in freebsd-vm's `prepare` step and cached with the VM image
  (`cache-after-prepare`); later runs restore that image and go straight
  to building. The cached ports tree also pins one ports commit per
  round until the `prepare-epoch` line in the workflow is bumped.
- poudriere's package directory is not in the VM at all. The runner
  workspace is mounted into the VM over the host's kernel NFS server
  (freebsd-vm's `sync: nfs` becomes anyvm's `sys-nfs` on Linux), and
  `vm_build.sh` points `/usr/local/poudriere/data/packages` at
  `work/pkgdata` on that mount. Before the VM starts, the runner seeds
  that directory with every package already published, under the
  original file names, so poudriere unqueues them instead of rebuilding;
  and every package poudriere writes is on the runner the moment it
  exists, even when the deadline watchdog interrupts the build. Two
  poudriere details make or break this: the seed must use poudriere's
  committed layout (`.latest/All`, not `.building/All`, which
  `convert_repository` moves away on a never-committed jail) and must
  include `Latest/pkg.pkg`, or `ensure_pkg_installed` deletes every
  existing package before looking at it. The NFS export squashes the
  guest's root to the runner's uid, so poudriere builds as root
  (`BUILD_AS_NON_ROOT=no`); as `nobody` the package phase cannot write
  its staging directory on that mount.
- A round is one workflow run in three stages. `plan` fetches the ledger,
  queues the requested origins (`config/pkglist.all` is the whole tree:
  the 30,742 originspecs a `poudriere bulk -a -n` dry run on riscv64
  leaves after IGNORED and skipped ports) and hashes everything still
  pending into K slices. `build` runs K jobs in parallel, one slice each,
  for up to 4.5 hours. `merge` unions their results, signs the touched
  shards in a short FreeBSD VM and publishes them once. Rounds repeat
  until nothing is pending.
- A build job seeds itself instead of downloading the repository: it
  dry-runs its slice (`poudriere bulk -n`), which lists the slice plus
  its whole dependency closure; every published package in that list is
  fetched from its shard release into poudriere's package directory, and
  the real build then unqueues those and builds only what nobody has
  built yet. Two jobs may still build the same not-yet-published
  dependency in one round; `merge` keeps one copy.
- `config/blacklist` keeps the runners off ports that cannot finish in a
  6-hour job under emulation (llvm, gcc, openjdk, ghc, go, libreoffice,
  mongodb). Those are built on a bigger machine and published through
  the same path. `lang/rust` refuses to build under qemu-user on its
  own, which takes every rust consumer out of scope.
- Packages are renamed to GitHub-safe asset names and uploaded to numbered
  shard releases. GitHub allows at most 1000 assets per release, so a full
  package set needs about 35 shards.
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
| `scripts/slice.py` | Hashes the pending origins into K stable slices, one per parallel job. |
| `scripts/wantlist.py` | Turns a slice's dry-run queue into the list of published packages to fetch before building. |
| `scripts/merge.py` | Unions the K jobs' result manifests; first job wins a duplicate dependency. |
| `scripts/mkshards.py` | Runs in the merge VM: stages each touched shard flat, runs `pkg repo` on it (index + signature), writes the ledger and the consumer config. |
| `scripts/publish.py` | Runs on the runner: fetches the ledger and open shard before signing, uploads shard releases after. |
| `scripts/seed.py` | Seeds poudriere's package directory from already-downloaded packages (single-job and local use; the matrix jobs seed themselves through `wantlist.py`). |
| `scripts/vm_build.sh` | Runs inside the build VM: emulation, jail, blacklist, selective seed, poudriere. |
| `config/blacklist` | Ports the runners never attempt. |
| `config/rebuild` | Ports to build again, once per tag, even when the ledger has them built or failed. |
| `config/ports-fixups.sh` | Edits to the ports tree before poudriere runs: workarounds for ports that cannot build under qemu-user as shipped. |
| `config/pkglist.all` | The whole tree for riscv64, as poudriere's dry run queued it. |
| `tests/` | Unit tests for the four pure modules. |

## Operating it

Start a round by hand:

```
gh workflow run build.yml --repo anyvm-org/freebsd-pkg-repo -f abi_slug=FreeBSD-15-riscv64 -f pkglist=config/pkglist.all -f slices=18 -f deadline=18000
```

Add `-f requeue=failed` (or `failed,oversize`) to give retired ports
another round, for example after adding a fixup.

The `Rounds` workflow keeps it going on its own: when a `Build` run
completes it dispatches the next round if the merge succeeded, re-runs
only the merge job (up to three attempts) if the merge failed, and stops
when the plan failed or no slice had work. Pause the loop with the
repository variable `AUTO_ROUNDS=off`; `ABI_SLUG`, `SLICES` and
`DEADLINE` variables override the defaults it dispatches with. A build
job whose VM dies mid-round is not retried: its slice simply comes back
in the next round, seeded with whatever the other jobs published.

Progress is the ledger on the index release:

```
gh release download idx-FreeBSD-15-riscv64 --repo anyvm-org/freebsd-pkg-repo -p ledger.json
```

### Rebuilding a published package

A package can be wrong without ever failing: round 10 found that
`devel/icu` had shipped `libicudata.so.76` without listing it in the
package's `shlibs_provided`, so `pkg` refused to install `boost-libs`
and everything above it. The ledger's dedup would normally skip a
rebuilt file of the same name. To replace it, list the port in
`config/rebuild` with a tag naming the fix:

```
devel/icu          icu-note-tag-2026-09-05
```

The next plan puts every flavor of that origin back to pending,
whatever its state (a `failed` port comes back with a clean count),
and the merge, which starts from the published ledger rather than the
plan's copy, applies the same file before it looks at the results, then
deletes the old asset before uploading the rebuilt one into the same
shard. The tag is remembered per port, so the line can
stay; change the tag to ask for another rebuild.

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

powerpc64 is listed in `config/abis` but blocked: pkg.freebsd.org has no
powerpc64 packages either, yet the `qemu-user-static` FreeBSD 15 ships
(3.1.0) aborts on every FreeBSD 15 powerpc64 binary, the newer
`qemu-user-static-devel` has no package on 15, and a native powerpc64
guest under QEMU is limited to one emulated CPU. It is parked, not
forgotten.

Only `FreeBSD:15:riscv64` exists so far. FreeBSD 13.x and 14.x images
have not been tested; the `pkg` they ship may predate ECDSA signature
verification, in which case they will need the manual `pkg-static add`
route above before `pkg update` can verify the repository.
