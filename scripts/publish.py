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
import fnmatch
import json
import os
import subprocess
import sys
import time

import shard


INDEX_ASSETS = frozenset(("data.pkg", "packagesite.pkg", "meta", "meta.conf",
                          "repo.pub"))


def is_package_asset(name):
    """True for a real package file, false for the repository's own index
    files. "gh release download --pattern '*.pkg'" cannot tell them apart,
    since data.pkg and packagesite.pkg end in .pkg too."""
    return name.endswith(".pkg") and name not in INDEX_ASSETS


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


DOWNLOAD_ATTEMPTS = 6
DOWNLOAD_RETRY_SECONDS = 20


def release_assets(repo, tag):
    """Names of the assets on a release, or None when the release does
    not exist."""
    probe = run(["gh", "release", "view", tag, "--repo", repo,
                 "--json", "assets", "--jq", ".assets[].name"],
                check=False)
    if probe.returncode != 0:
        return None
    return [line.strip() for line in probe.stdout.splitlines() if line.strip()]


def download_assets(repo, tag, pattern, dest, sleep=time.sleep):
    """Fetch the assets matching pattern. Returns False only when the
    release or the asset genuinely does not exist; a download that fails
    is retried and then raised, never reported as "nothing there".

    Run 33733013149 planned a whole round from an empty ledger because a
    transient "connection reset by peer" on the asset CDN was taken for
    "no ledger published yet"; in the merge job the same mistake would
    have PUBLISHED that empty ledger over the real one.
    """
    names = release_assets(repo, tag)
    if names is None:
        return False
    if not fnmatch.filter(names, pattern):
        return False
    os.makedirs(dest, exist_ok=True)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        probe = run(["gh", "release", "download", tag, "--repo", repo,
                     "--pattern", pattern, "--dir", dest, "--clobber"],
                    check=False)
        if probe.returncode == 0:
            return True
        print("download of %s from %s failed (attempt %d/%d)"
              % (pattern, tag, attempt, DOWNLOAD_ATTEMPTS))
        if attempt < DOWNLOAD_ATTEMPTS:
            sleep(DOWNLOAD_RETRY_SECONDS)
    raise RuntimeError("could not download %s from release %s after %d attempts"
                       % (pattern, tag, DOWNLOAD_ATTEMPTS))


UPLOAD_ATTEMPTS = 4


def upload_batch(repo, tag, batch, sleep=time.sleep):
    """gh release upload --clobber for one batch, retried. Uploading 800
    assets is 16 calls against an API that does fail now and then (run
    33841503080 died on the 11th batch, and the CalledProcessError hid
    gh's own message); --clobber makes a retry idempotent."""
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        probe = run(["gh", "release", "upload", tag, "--repo", repo,
                     "--clobber"] + batch, check=False)
        if probe.returncode == 0:
            return
        print("upload of %d assets to %s failed (attempt %d/%d): %s"
              % (len(batch), tag, attempt, UPLOAD_ATTEMPTS,
                 (probe.stderr or "").strip()[-400:]))
        if attempt < UPLOAD_ATTEMPTS:
            sleep(DOWNLOAD_RETRY_SECONDS)
    raise RuntimeError("could not upload a batch of %d assets to %s after %d attempts"
                       % (len(batch), tag, UPLOAD_ATTEMPTS))


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
    count = len([f for f in os.listdir(dest) if is_package_asset(f)])
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
            upload_batch(args.repo, tag, paths[start:start + 50])
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
