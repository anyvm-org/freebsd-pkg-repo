"""Publish built packages as GitHub Release assets and republish the index.

Two subcommands, matching the two halves of the workflow:

  upload-shards   run by a builder job: rename packages to safe asset names,
                  assign shards, upload. Never touches the ledger, so
                  parallel builders cannot race each other.
  publish-index   run by the single collector job: merge result manifests,
                  rewrite packagesite.yaml onto the shards, and replace the
                  index release assets.

Uses the gh CLI, which is preinstalled on GitHub-hosted runners.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

import ledger
import repoindex
import sanitize
import shard

TWO_GIB = 2 * 1024 ** 3


def run(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True)


def ensure_release(repo, tag, title):
    """Create the release if it does not exist yet. Idempotent."""
    probe = subprocess.run(["gh", "release", "view", tag, "--repo", repo],
                           capture_output=True, text=True)
    if probe.returncode == 0:
        return
    run(["gh", "release", "create", tag, "--repo", repo, "--title", title,
         "--notes", "Managed by freebsd-pkg-repo. Do not edit by hand."])


def upload_asset(repo, tag, path):
    run(["gh", "release", "upload", tag, path, "--repo", repo, "--clobber"])


def download_asset(repo, tag, name, dest):
    """Return True if the asset existed and was fetched."""
    probe = subprocess.run(
        ["gh", "release", "download", tag, "--repo", repo,
         "--pattern", name, "--dir", dest, "--clobber"],
        capture_output=True, text=True)
    return probe.returncode == 0


def load_ledger(repo, abi_slug, workdir):
    """Fetch the published ledger, or None on the very first run."""
    tag = shard.index_tag(abi_slug)
    if download_asset(repo, tag, "ledger.json", workdir):
        with open(os.path.join(workdir, "ledger.json")) as handle:
            return json.load(handle)
    return None


def existing_assignments(led):
    """Rebuild {safe_asset_name: shard_index} from a ledger."""
    existing = {}
    if not led:
        return existing
    for entry in led["ports"].values():
        if entry.get("pkgfile") and entry.get("shard") is not None:
            existing[sanitize.sanitize_asset_name(entry["pkgfile"])] = \
                entry["shard"]
    return existing


def cmd_upload_shards(args):
    """Rename, shard and upload every .pkg under --packages-dir."""
    led = load_ledger(args.repo, args.abi_slug, args.workdir)
    existing = existing_assignments(led)

    originals = []
    paths = {}
    for root, _dirs, files in os.walk(args.packages_dir):
        for name in files:
            if name.endswith(".pkg"):
                originals.append(name)
                paths[name] = os.path.join(root, name)
    originals.sort()
    safe_of = sanitize.sanitize_all(originals)

    oversize = {}
    uploadable = []
    for original in originals:
        size = os.path.getsize(paths[original])
        if size > TWO_GIB:
            # GitHub refuses release assets over 2 GiB. Record and skip;
            # the ledger marks the port oversize so it is not retried.
            oversize[original] = size
            continue
        uploadable.append(original)

    assignments, _counts = shard.assign_shards(
        existing, [safe_of[name] for name in uploadable])

    staging = os.path.join(args.workdir, "staging")
    os.makedirs(staging, exist_ok=True)
    published = {}
    for original in uploadable:
        safe = safe_of[original]
        tag = shard.shard_tag(args.abi_slug, assignments[safe])
        staged = os.path.join(staging, safe)
        shutil.copyfile(paths[original], staged)
        ensure_release(args.repo, tag, tag)
        upload_asset(args.repo, tag, staged)
        published[safe] = tag
        os.remove(staged)

    with open(args.out, "w") as handle:
        json.dump({"published": published, "oversize": oversize},
                  handle, indent=2, sort_keys=True)
    print("uploaded %d assets, %d oversize" % (len(published), len(oversize)))
    return 0


def cmd_publish_index(args):
    """Merge results, rewrite repopaths, replace the index release assets."""
    index = shard.index_tag(args.abi_slug)
    ensure_release(args.repo, index, index)

    led = load_ledger(args.repo, args.abi_slug, args.workdir)
    if led is None:
        with open(args.origins) as handle:
            origins = [line.strip() for line in handle
                       if line.strip() and not line.startswith("#")]
        led = ledger.new_ledger(args.abi, args.ports_commit, origins)

    published = {}
    for path in args.results:
        with open(path) as handle:
            payload = json.load(handle)
        if "published" in payload:
            published.update(payload["published"])
            if payload.get("oversize"):
                ledger.merge_result(
                    led, {"oversize": payload["oversize"]}, args.now)
        else:
            ledger.merge_result(led, payload, args.now)

    safe_of = {}
    shard_of = {}
    for entry in led["ports"].values():
        pkgfile = entry.get("pkgfile")
        if not pkgfile:
            continue
        safe = sanitize.sanitize_asset_name(pkgfile)
        tag = published.get(safe)
        if tag is None:
            if entry.get("shard") is None:
                continue
            tag = shard.shard_tag(args.abi_slug, entry["shard"])
        entry["shard"] = int(tag.rsplit("-", 1)[1])
        safe_of[pkgfile] = safe
        shard_of[safe] = tag

    with open(args.packagesite) as handle:
        rewritten = list(repoindex.rewrite_stream(handle, shard_of, safe_of))
    with open(args.packagesite, "w") as handle:
        for line in rewritten:
            handle.write(line + "\n")
    print("rewrote %d manifest entries" % len(rewritten))

    led_path = os.path.join(args.workdir, "ledger.json")
    with open(led_path, "w") as handle:
        json.dump(led, handle, indent=2, sort_keys=True)

    for path in [args.packagesite, led_path] + list(args.extra):
        if os.path.exists(path):
            upload_asset(args.repo, index, path)

    print("index published; done=%s" % ledger.is_done(led))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--abi-slug", required=True)
    parser.add_argument("--workdir", default="work")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("upload-shards")
    up.add_argument("--packages-dir", required=True)
    up.add_argument("--out", required=True)
    up.set_defaults(func=cmd_upload_shards)

    idx = sub.add_parser("publish-index")
    idx.add_argument("--abi", required=True)
    idx.add_argument("--ports-commit", required=True)
    idx.add_argument("--origins", required=True)
    idx.add_argument("--packagesite", required=True)
    idx.add_argument("--now", required=True)
    idx.add_argument("--results", nargs="+", required=True)
    idx.add_argument("--extra", nargs="*", default=[])
    idx.set_defaults(func=cmd_publish_index)

    args = parser.parse_args(argv)
    os.makedirs(args.workdir, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
