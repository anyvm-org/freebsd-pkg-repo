"""Assign package assets to GitHub Release shards.

GitHub documents "Up to 1000 release assets may be associated with a single
release" and enforces it with HTTP 422, so a full FreeBSD package set
(38169 packages on amd64) is spread over roughly 40 shard releases.

Each shard release is a complete, self-contained pkg repository: its own
meta.conf, data.pkg, packagesite.pkg and repo.pub, all written and signed
by "pkg repo" inside the build VM, plus the packages themselves at the
release root. With packages at the root, pkg repo records every repopath
as a bare filename, which is exactly the shape of a GitHub release asset
URL -- so nothing is rewritten and nothing is re-signed. Consumers get one
repo block per shard; pkg resolves dependencies across them (verified
with real riscv64 packages on 2026-09-02).

Shards fill sequentially: new packages always land in the current open
shard, and a new one is opened only when that is full. An assignment is
permanent. Once the ledger records a package's shard, its download URL is
fixed; moving it would break every consumer that cached the old URL.
"""

# Four index files also count as assets in every shard release, and a
# little margin is kept under GitHub's hard limit of 1000.
SHARD_CAPACITY = 985


def shard_tag(abi_slug, index):
    """Release tag holding package assets, e.g. pkg-FreeBSD-15-riscv64-007."""
    return "pkg-%s-%03d" % (abi_slug, index)


def index_tag(abi_slug):
    """Release tag holding meta.conf, packagesite.pkg, data.pkg, ledger.json."""
    return "idx-%s" % abi_slug


def assign_shards(existing, new_names, capacity=SHARD_CAPACITY):
    """Place new_names into shards without moving anything already placed.

    existing:  {asset_name: shard_index} from the ledger
    new_names: iterable of asset names that may or may not be placed yet
    Returns (assignments, counts).
    """
    counts = {}
    for index in existing.values():
        counts[index] = counts.get(index, 0) + 1
    assignments = dict(existing)
    current = max(counts) if counts else 0
    for name in new_names:
        if name in assignments:
            continue
        while counts.get(current, 0) >= capacity:
            current += 1
        assignments[name] = current
        counts[current] = counts.get(current, 0) + 1
    return assignments, counts
