"""Turn a poudriere bulk log directory into a result manifest.

Runs inside the build VM at the end of vm_build.sh, and again on the
runner when the VM died before that (run 33841503080 lost a whole job's
packages that way: "Timeout, server 127.0.0.1 not responding", no
result.json, nothing collected). The log directory therefore lives on
the runner too, next to the packages.

poudriere writes these files into the build log dir (common.sh
~10260). Field order comes from the badd call sites:
  ports.built    originspec pkgname elapsed
  ports.failed   originspec pkgname phase errortype elapsed
  ports.ignored  originspec pkgname reason...
  ports.skipped  originspec pkgname cause_pkgname
"""

import argparse
import json
import os
import sys


def column(logdir, name, index):
    path = os.path.join(logdir, ".poudriere.ports." + name)
    rows = []
    if os.path.exists(path):
        with open(path) as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > index:
                    rows.append((fields[0], fields[index]))
    return rows


def interrupted_ports(logdir, exclude):
    """Origins whose per-port log has a "=>> Building" header and no
    "ended at" footer: the deadline watchdog (or a dead VM) cut them
    short."""
    found = []
    logs_dir = os.path.join(logdir, "logs")
    if not os.path.isdir(logs_dir):
        return found
    for name in sorted(os.listdir(logs_dir)):
        if not name.endswith(".log"):
            continue
        path = os.path.join(logs_dir, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        with open(path, errors="replace") as handle:
            first = handle.readline().strip()
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            tail = handle.read()
        if first.startswith("=>> Building ") and " ended at" not in tail:
            origin = first[len("=>> Building "):].strip()
            if origin and origin not in exclude:
                found.append(origin)
    return sorted(set(found))


def manifest(logdir):
    built = {}
    for origin, pkgname in column(logdir, "built", 1):
        built[origin] = pkgname + ".pkg"

    ignored = [origin for origin, _ in column(logdir, "ignored", 1)]

    # A skipped port has an ignored or failed port somewhere in its
    # dependency chain. poudriere names the cause by package name; map
    # it back to the origin through this job's ignored and failed
    # columns so the ledger can watch that port and release the
    # dependents once it is built. Until then they are "blocked", not
    # pending: listing them again just re-derives the same skip and,
    # worse, drags the blocker back in as a dependency (round 15 spent
    # four builders per job on grpc timing out, 114 packages in all).
    origin_of = {}
    for name in ("ignored", "failed"):
        for origin, pkgname in column(logdir, name, 1):
            origin_of.setdefault(pkgname, origin)
    skipped = {}
    for origin, cause in column(logdir, "skipped", 2):
        if origin not in built:
            skipped[origin] = origin_of.get(cause, cause)

    # A "timeout" is the phase limit on this machine, not a broken port:
    # oversize, to be built on a bigger one.
    failed = []
    oversize = {}
    for origin, errortype in column(logdir, "failed", 3):
        if errortype == "timeout":
            oversize[origin] = "timeout on the CI runner"
        else:
            failed.append(origin)

    exclude = set(built) | set(failed) | set(oversize)
    return {
        "built": built,
        "failed": failed,
        "ignored": sorted(set(ignored)),
        "oversize": oversize,
        "interrupted": interrupted_ports(logdir, exclude),
        "skipped": skipped,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logdir", required=True,
                        help="poudriere's logs/bulk/<jail>-<tree>/latest")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if not os.path.isdir(args.logdir):
        print("result: no log directory at %s" % args.logdir, file=sys.stderr)
        return 1
    result = manifest(args.logdir)
    with open(args.out, "w") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps(dict((k, len(v)) for k, v in result.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
