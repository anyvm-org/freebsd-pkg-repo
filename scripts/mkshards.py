"""Turn a finished poudriere build into ready-to-upload shard releases.

Runs INSIDE the FreeBSD build VM after vm_build.sh, because every shard's
index is produced by "pkg repo" -- a FreeBSD tool -- and signed by it in the
same pass. Nothing here rewrites a manifest or constructs a signature; the
repository index is always pkg's own.

The trick that makes this work: packages are staged at the ROOT of the
shard directory, not under All/. pkg repo then records every repopath as a
bare filename, and <release-url>/<filename> is exactly a GitHub release
asset URL. Verified end to end with real riscv64 packages on 2026-09-02,
including dependency resolution across two shards.

Inputs
  --result     out/result.json from vm_build.sh
  --packages   poudriere's All/ directory with the freshly built .pkg files
  --ledger     the published ledger.json, absent on the very first run
  --existing   <dir>/<shard-tag>/*.pkg: already-published packages of the
               open shard, downloaded by the runner so that shard's index
               can be regenerated from everything it contains
  --key        RSA private key for pkg repo
  --pubkey     matching public key, copied into every shard as repo.pub
  --repo       owner/name, for the consumer config URLs
  --abi-slug   e.g. FreeBSD-15-riscv64
  --out        output directory

Outputs under --out
  shards/<tag>/   meta.conf data.pkg packagesite.pkg repo.pub *.pkg
  ledger.json     updated
  anyvm.conf      one repo block per shard, for consumers
  upload.json     what publish.py uploads and deletes, per tag
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

import ledger
import sanitize
import shard

REPO_NAME_PREFIX = "anyvm"
PUBKEY_PATH = "/usr/local/etc/pkg/keys/anyvm.pub"
INDEX_FILES = ("meta.conf", "data.pkg", "packagesite.pkg")

# pkg-repo(8) selects the signer by a prefix on the key path: "rsa:" (the
# default when omitted), "ecdsa:", or "eddsa:" (alias "ecc:"). Always
# write the prefix explicitly so the choice is visible in the log.
KEY_TYPES = ("rsa", "ecdsa", "eddsa")


def key_argument(key_type, path):
    """The <signer-type>:<keyfile> argument pkg repo expects."""
    if key_type not in KEY_TYPES:
        raise ValueError("unknown key type %r; expected one of %s"
                         % (key_type, ", ".join(KEY_TYPES)))
    return "%s:%s" % (key_type, path)


def plan(led, built, now, capacity=shard.SHARD_CAPACITY):
    """Decide which shard each new package goes to, and update the ledger.

    built: {origin: original_pkgfile}
    Returns {shard_index: {"new": {safe: original},
                           "existing": [safe],   # already in the shard
                           "delete": [safe]}}    # superseded in the shard
    covering only shards that receive at least one new package.
    """
    safe_of = sanitize.sanitize_all(built.values())

    previous = {}
    for origin in built:
        entry = led["ports"].get(origin)
        if entry and entry.get("shard") is not None and entry.get("pkgfile"):
            previous[origin] = (entry["pkgfile"], entry["shard"])

    # A job may rebuild a package that is already published under the
    # same file name (its seed fetch failed, or poudriere decided to
    # rebuild). That is not a new package: uploading it again would
    # touch a possibly full, possibly old shard whose index cannot be
    # regenerated without every one of its files (run 33865909605:
    # "OpenSP-1.5.2_4.pkg is recorded in shard ...-000 but was not
    # downloaded"). The published copy stays; the ledger is untouched.
    duplicates = sorted(origin for origin in built
                        if origin in previous
                        and previous[origin][0] == safe_of[built[origin]])
    if duplicates:
        print("plan: %d rebuilt packages already published, kept as is: %s"
              % (len(duplicates), " ".join(duplicates[:8])
                 + (" ..." if len(duplicates) > 8 else "")))
        built = dict((o, f) for o, f in built.items() if o not in duplicates)

    existing = {}
    for entry in led["ports"].values():
        if entry.get("shard") is not None and entry.get("pkgfile"):
            existing[entry["pkgfile"]] = entry["shard"]

    new_names = [safe_of[built[origin]] for origin in sorted(built)]
    assignments, _counts = shard.assign_shards(existing, new_names, capacity)

    result = {}
    for origin in sorted(built):
        original = built[origin]
        safe = safe_of[original]
        index = assignments[safe]
        spec = result.setdefault(index, {"new": {}, "existing": [],
                                         "delete": []})
        spec["new"][safe] = original
        old = previous.get(origin)
        if old and old[1] == index and old[0] != safe:
            spec["delete"].append(old[0])

    for index, spec in result.items():
        gone = set(spec["delete"]) | set(spec["new"])
        spec["existing"] = sorted(name for name, i in existing.items()
                                  if i == index and name not in gone)
        spec["delete"].sort()

    ledger.merge_result(
        led, {"built": dict((o, safe_of[built[o]]) for o in built)}, now)
    for origin in built:
        entry = led["ports"][origin]
        entry["shard"] = assignments[safe_of[built[origin]]]
        # Sanitising is not reversible (',' and '+' both become '-'), and
        # poudriere looks for packages by their original file name, so
        # seeding a later build from published assets needs this kept.
        entry["pkgfile_orig"] = built[origin]
    return result


def generate_conf(led, repo, abi_slug):
    """The consumer's /usr/local/etc/pkg/repos/anyvm.conf: one block per
    shard from 0 to the highest shard the ledger knows about."""
    highest = 0
    for entry in led["ports"].values():
        if entry.get("shard") is not None:
            highest = max(highest, entry["shard"])
    blocks = []
    for index in range(highest + 1):
        tag = shard.shard_tag(abi_slug, index)
        blocks.append(
            "%s-%03d: {\n"
            "  url: \"https://github.com/%s/releases/download/%s\",\n"
            "  enabled: yes,\n"
            "  signature_type: \"pubkey\",\n"
            "  pubkey: \"%s\"\n"
            "}\n" % (REPO_NAME_PREFIX, index, repo, tag, PUBKEY_PATH))
    return "\n".join(blocks)


def fatal(msg):
    print("FATAL: " + msg, file=sys.stderr)
    sys.exit(1)


def fetch_asset(url, dest, attempts=4):
    """Download one release asset (GitHub answers with a redirect to its
    CDN; urllib follows it). True on success."""
    import time
    import urllib.request
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, \
                    open(dest, "wb") as handle:
                shutil.copyfileobj(response, handle)
            if os.path.getsize(dest) > 0:
                return True
        except Exception as exc:  # network, HTTP, disk: retry, then fail
            print("fetch %s failed (attempt %d/%d): %s" % (url, attempt, attempts, exc))
        if attempt < attempts:
            time.sleep(15)
    return False


def find_package(dirs, name):
    """First directory in dirs holding name, or None.

    A bulk that the deadline watchdog interrupted never runs poudriere's
    "committing packages" step, so its finished packages are not under
    .latest/All but in the in-progress directory. Callers pass every
    candidate directory in preference order.
    """
    for directory in dirs:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def execute(spec_by_shard, args):
    """Stage each touched shard, run pkg repo on it, write the outputs."""
    shards_dir = os.path.join(args.out, "shards")
    upload = {"upload": {}, "delete": {}}

    for index in sorted(spec_by_shard):
        spec = spec_by_shard[index]
        tag = shard.shard_tag(args.abi_slug, index)
        stage = os.path.join(shards_dir, tag)
        if os.path.isdir(stage):
            shutil.rmtree(stage)
        os.makedirs(stage)

        missing = [safe for safe in spec["existing"]
                   if not os.path.isfile(os.path.join(args.existing, tag, safe))]
        if missing:
            # An older shard touched by a superseded package: the runner
            # only downloaded the open shard. Its index cannot be
            # regenerated without every file, so fetch the rest here.
            print("fetching %d published packages of %s not downloaded on the runner"
                  % (len(missing), tag))
            os.makedirs(os.path.join(args.existing, tag), exist_ok=True)
            for safe in missing:
                url = "https://github.com/%s/releases/download/%s/%s" % (
                    args.repo, tag, safe)
                dest = os.path.join(args.existing, tag, safe)
                if not fetch_asset(url, dest):
                    fatal("%s is recorded in shard %s, was not downloaded to %s "
                          "and could not be fetched from %s"
                          % (safe, tag, dest, url))
        for safe in spec["existing"]:
            shutil.copyfile(os.path.join(args.existing, tag, safe),
                            os.path.join(stage, safe))

        for safe, original in spec["new"].items():
            src = find_package(args.packages, original)
            if src is None:
                fatal("built package %s not found under any of: %s"
                      % (original, ", ".join(args.packages)))
            shutil.copyfile(src, os.path.join(stage, safe))

        print("pkg repo %s (%d existing + %d new, %d superseded)"
              % (tag, len(spec["existing"]), len(spec["new"]),
                 len(spec["delete"])))
        subprocess.run(["pkg", "repo", stage,
                        key_argument(args.key_type, args.key)], check=True)
        for name in INDEX_FILES:
            if not os.path.isfile(os.path.join(stage, name)):
                fatal("pkg repo did not produce %s in %s" % (name, stage))
        shutil.copyfile(args.pubkey, os.path.join(stage, "repo.pub"))

        upload["upload"][tag] = sorted(
            os.path.join(stage, f) for f in os.listdir(stage))
        upload["delete"][tag] = spec["delete"]

    return upload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True)
    parser.add_argument("--packages", action="append", required=True,
                        help="directory holding built .pkg files; repeat "
                             "in preference order (committed dir first, "
                             "then poudriere's in-progress dir)")
    parser.add_argument("--ledger")
    parser.add_argument("--existing", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--key-type", choices=KEY_TYPES, default="rsa")
    parser.add_argument("--pubkey", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--abi", required=True)
    parser.add_argument("--abi-slug", required=True)
    parser.add_argument("--origins", required=True)
    parser.add_argument("--ports-commit", required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    with open(args.result) as handle:
        result = json.load(handle)

    with open(args.origins) as handle:
        origins = [line.strip() for line in handle
                   if line.strip() and not line.startswith("#")]
    if args.ledger and os.path.isfile(args.ledger):
        with open(args.ledger) as handle:
            led = json.load(handle)
        # The slice being built may list ports the ledger has never seen
        # (a new pkglist against a ledger made for an earlier one); they
        # are pending until poudriere reports them built.
        added = ledger.add_origins(led, origins)
        if added:
            print("ledger: %d new origins queued from %s"
                  % (len(added), args.origins))
    else:
        led = ledger.new_ledger(args.abi, args.ports_commit, origins)

    # poudriere reports a port's default flavor by its real name
    # (devel/git@default, devel/py-foo@py312); the list, and so the
    # ledger, names it bare. Re-key the result before anything reads it.
    listed = set(origins)
    moved = ledger.canonicalise(led, listed)
    if moved:
        print("ledger: %d entries re-keyed onto their listed origin" % len(moved))
    result = {
        "built": dict((ledger.canonical_origin(listed, o), f)
                      for o, f in result.get("built", {}).items()),
        "failed": [ledger.canonical_origin(listed, o) for o in result.get("failed", [])],
        "ignored": [ledger.canonical_origin(listed, o) for o in result.get("ignored", [])],
        "oversize": dict((ledger.canonical_origin(listed, o), v)
                         for o, v in result.get("oversize", {}).items()),
        "interrupted": [ledger.canonical_origin(listed, o)
                        for o in result.get("interrupted", [])],
    }

    # failures / ignores / oversize first, then the built set via plan()
    ledger.merge_result(led, {"failed": result["failed"],
                              "ignored": result["ignored"],
                              "oversize": result["oversize"],
                              "interrupted": result["interrupted"]},
                        args.now)

    os.makedirs(args.out, exist_ok=True)
    spec_by_shard = plan(led, result["built"], args.now)
    upload = execute(spec_by_shard, args)

    with open(os.path.join(args.out, "ledger.json"), "w") as handle:
        json.dump(led, handle, indent=2, sort_keys=True)
    with open(os.path.join(args.out, "anyvm.conf"), "w") as handle:
        handle.write(generate_conf(led, args.repo, args.abi_slug))
    with open(os.path.join(args.out, "upload.json"), "w") as handle:
        json.dump(upload, handle, indent=2, sort_keys=True)

    print("shards touched: %s; pending=%d done=%s"
          % (sorted(spec_by_shard), len(ledger.pending_origins(led)),
             ledger.is_done(led)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
