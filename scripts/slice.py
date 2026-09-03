"""Split the ledger's pending origins into K disjoint slices.

One poudriere job builds one slice; K jobs run in parallel on K runners,
each seeded with every published package, and a merge job publishes the
union. Dependencies are poudriere's business: a job builds whatever its
slice needs that is not published yet, so two slices may build the same
dependency in the same round (wasted, not wrong; the merge keeps one
copy). As the published set grows that duplication disappears.

Assignment is by a stable hash of the origin, not by position, so a
port keeps its slice from round to round and a slice that failed on a
port keeps retrying the same port until MAX_FAILURES retires it.

Usage:
  slice.py --ledger work/ledger.json --slices 16 --index 3 --out slice.txt
  slice.py --ledger work/ledger.json --slices 16 --count   (origins per slice)
"""

import argparse
import hashlib
import json
import sys

import ledger


def slice_of(origin, slices):
    """Stable slice index for an origin (sha1, not hash(): that is salted
    per process)."""
    digest = hashlib.sha1(origin.encode("ascii")).digest()
    return int.from_bytes(digest[:4], "big") % slices


def assign(origins, slices):
    """Return [list_of_origins] * slices, each sorted."""
    buckets = [[] for _ in range(slices)]
    for origin in sorted(origins):
        buckets[slice_of(origin, slices)].append(origin)
    return buckets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--slices", type=int, required=True)
    parser.add_argument("--index", type=int)
    parser.add_argument("--out")
    parser.add_argument("--count", action="store_true")
    args = parser.parse_args(argv)
    if args.slices < 1:
        parser.error("--slices must be >= 1")

    with open(args.ledger) as handle:
        led = json.load(handle)
    buckets = assign(ledger.pending_origins(led), args.slices)

    if args.count:
        for index, bucket in enumerate(buckets):
            print("%d %d" % (index, len(bucket)))
        return 0
    if args.index is None or args.out is None:
        parser.error("--index and --out are required without --count")
    if not 0 <= args.index < args.slices:
        parser.error("--index out of range")
    with open(args.out, "w") as handle:
        for origin in buckets[args.index]:
            handle.write(origin + "\n")
    print("slice %d/%d: %d origins -> %s"
          % (args.index, args.slices, len(buckets[args.index]), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
