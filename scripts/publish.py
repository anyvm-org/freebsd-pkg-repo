"""Runner-side half of publishing. Two subcommands:

  prepare   before the VM starts: fetch the published ledger and the
            packages of the open shard, so mkshards.py inside the VM can
            regenerate that shard's index from everything in it.
  publish   after the VM: upload every staged shard directory as release
            assets, delete superseded assets, and refresh the index release
            (ledger.json, anyvm.conf, repo.pub).

pkg repo runs in the VM (it is a FreeBSD tool); this script never touches
a manifest or a signature. Uses the gh CLI, preinstalled on runners.
"""

import argparse
import json
import os
import subprocess
import sys

import shard


def run(argv, check=True):
    return subprocess.run(argv, check=check, capture_output=True, text=True)


def release_exists(repo, tag):
    return run(["gh", "release", "view", tag, "--repo", repo],
               check=False).returncode == 0


def ensure_release(repo, tag):
    if release_exists(repo, tag):
        return
    run(["gh", "release", "create", tag, "--repo", repo, "--title", tag,
         "--notes", "Managed by freebsd-pkg-repo. Do not edit by hand."])
    print("created release %s" % tag)


def download_assets(repo, tag, pattern, dest):
    """Return True if at least one matching asset was fetched."""
    os.makedirs(dest, exist_ok=True)
    probe = run(["gh", "release", "download", tag, "--repo", repo,
                 "--pattern", pattern, "--dir", dest, "--clobber"],
                check=False)
    return probe.returncode == 0


def cmd_prepare(args):
    index = shard.index_tag(args.abi_slug)
    os.makedirs(args.workdir, exist_ok=True)
    have_ledger = download_assets(args.repo, index, "ledger.json",
                                  args.workdir)
    if not have_ledger:
        print("no published ledger yet: first run")
        os.makedirs(args.existing, exist_ok=True)
        return 0

    with open(os.path.join(args.workdir, "ledger.json")) as handle:
        led = json.load(handle)
    highest = -1
    for entry in led["ports"].values():
        if entry.get("shard") is not None:
            highest = max(highest, entry["shard"])
    if highest < 0:
        print("ledger has no shards yet")
        os.makedirs(args.existing, exist_ok=True)
        return 0

    tag = shard.shard_tag(args.abi_slug, highest)
    dest = os.path.join(args.existing, tag)
    print("open shard is %s; fetching its packages" % tag)
    download_assets(args.repo, tag, "*.pkg", dest)
    count = len([f for f in os.listdir(dest) if f.endswith(".pkg")])
    print("fetched %d packages into %s" % (count, dest))
    return 0


def cmd_publish(args):
    with open(args.upload_json) as handle:
        upload = json.load(handle)

    for tag in sorted(upload.get("upload", {})):
        ensure_release(args.repo, tag)
        for name in upload.get("delete", {}).get(tag, []):
            run(["gh", "release", "delete-asset", tag, name,
                 "--repo", args.repo, "--yes"], check=False)
            print("deleted %s from %s" % (name, tag))
        paths = upload["upload"][tag]
        # gh accepts many files per call; batch to keep argv sane
        for start in range(0, len(paths), 50):
            batch = paths[start:start + 50]
            run(["gh", "release", "upload", tag, "--repo", args.repo,
                 "--clobber"] + batch)
        print("uploaded %d assets to %s" % (len(paths), tag))

    index = shard.index_tag(args.abi_slug)
    ensure_release(args.repo, index)
    extras = [os.path.join(args.out, n) for n in ("ledger.json", "anyvm.conf")]
    extras.append(args.pubkey)
    extras = [p for p in extras if os.path.isfile(p)]
    run(["gh", "release", "upload", index, "--repo", args.repo, "--clobber"]
        + extras)
    print("index release %s refreshed with %s"
          % (index, [os.path.basename(p) for p in extras]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--abi-slug", required=True)
    parser.add_argument("--workdir", default="work")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare")
    prep.add_argument("--existing", required=True)
    prep.set_defaults(func=cmd_prepare)

    pub = sub.add_parser("publish")
    pub.add_argument("--upload-json", required=True)
    pub.add_argument("--out", required=True)
    pub.add_argument("--pubkey", required=True)
    pub.set_defaults(func=cmd_publish)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
