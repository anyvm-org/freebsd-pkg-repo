"""Turn a poudriere dry-run queue into the list of published packages to
fetch before the real build.

A build job must not download the whole repository (tens of GB once the
tree is in), and poudriere's own PACKAGE_FETCH_URL wants a single
repository URL, which a set of shard releases is not. So the job runs
"poudriere bulk -n" on its slice first; that writes every queued
originspec and package name -- the slice plus its whole dependency
closure -- to .poudriere.ports.queued. Everything in that list that the
ledger says is built and published is fetched from its shard release,
under its original file name, into the seed layout; poudriere then
unqueues it ("Unqueueing existing packages") and builds only the rest.

Output: one "URL<TAB>original-name" line per package to fetch.
"""

import argparse
import json
import sys

import shard


def read_queued(path):
    """originspec -> pkgname from .poudriere.ports.queued (two columns;
    the port's flavor is part of the originspec)."""
    queued = {}
    with open(path) as handle:
        for line in handle:
            fields = line.split()
            if len(fields) >= 2:
                queued[fields[0]] = fields[1]
    return queued


def wanted(led, queued, repo, abi_slug):
    """[(url, original_name)] for every queued origin the ledger has as
    built with a shard. Bare and flavored spellings are both tried, since
    the ledger keys a flavored port the way poudriere reported it."""
    ports = led.get("ports", {})
    out = []
    seen = set()
    for originspec in sorted(queued):
        entry = ports.get(originspec)
        if entry is None and "@" in originspec:
            entry = ports.get(originspec.split("@", 1)[0])
        if not entry or entry.get("state") != "built":
            continue
        safe = entry.get("pkgfile")
        index = entry.get("shard")
        if not safe or index is None:
            continue
        original = entry.get("pkgfile_orig") or safe
        if original in seen:
            continue
        seen.add(original)
        url = "https://github.com/%s/releases/download/%s/%s" % (
            repo, shard.shard_tag(abi_slug, index), safe)
        out.append((url, original))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--queued", required=True,
                        help="poudriere's .poudriere.ports.queued")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--abi-slug", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    with open(args.ledger) as handle:
        led = json.load(handle)
    queued = read_queued(args.queued)
    pairs = wanted(led, queued, args.repo, args.abi_slug)
    with open(args.out, "w") as handle:
        for url, original in pairs:
            handle.write("%s\t%s\n" % (url, original))
    print("wantlist: %d of %d queued packages are published; fetch list -> %s"
          % (len(pairs), len(queued), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
