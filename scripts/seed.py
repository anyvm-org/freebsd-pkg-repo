"""Seed poudriere's package directory with the published packages.

poudriere unqueues a queued port when a current package for it already
exists in the package directory, so seeding that directory with what is
published lets a slice larger than one BUILD_DEADLINE finish across jobs
instead of starting from zero each time (run 33593421366 built 92 of 115
in 4.5 h and the next run would have rebuilt all of them).

The LAYOUT matters. poudriere's prepare_build (common.sh) does, in order:
  1. no .latest symlink -> convert_repository: every top-level directory
     of the package root, whatever its name, is moved into a fresh
     .real_<epoch>/ and .latest is pointed at that;
  2. .building exists -> reuse it; else clone .latest into .building.
So a package dropped into .building/All of a never-committed root is
moved to .real_<epoch>/.building/All by step 1 and never seen (measured
2026-09-02: all five seeded packages rebuilt). The layout poudriere
itself leaves after a commit is what works: .real_<epoch>/All/*.pkg,
.latest -> .real_<epoch>, All -> .latest/All. Step 1 is then skipped and
step 2 hard-links the seeded packages into .building, where the usual
staleness checks (version, options, dependencies) vet each of them.

Published assets carry sanitised names; poudriere wants the original
package file name. The ledger records it as pkgfile_orig (entries written
before that field existed carry only names that needed no sanitising, so
the safe name is the original for them).

Usage (on the runner, before the VM starts):
  seed.py --ledger work/ledger.json --existing work/existing \
          --dest work/pkgdata/packages/<jail>-default
"""

import argparse
import json
import os
import shutil
import sys
import time


def seed_plan(led, existing_dirs):
    """Return [(source_path, original_name)] for every built package in the
    ledger whose safe-named file is present in one of existing_dirs.
    Missing files are skipped: seeding is an optimisation, the build is
    correct without it."""
    pairs = []
    for origin in sorted(led.get("ports", {})):
        entry = led["ports"][origin]
        if entry.get("state") != "built" or not entry.get("pkgfile"):
            continue
        safe = entry["pkgfile"]
        original = entry.get("pkgfile_orig") or safe
        for directory in existing_dirs:
            path = os.path.join(directory, safe)
            if os.path.isfile(path):
                pairs.append((path, original))
                break
    return pairs


def seed_layout(root, epoch):
    """Make root look like a committed poudriere package repository and
    return the All directory to put packages in. A root that already has
    a .latest symlink is left as it is (its .latest/All is returned)."""
    latest = os.path.join(root, ".latest")
    if os.path.islink(latest):
        all_dir = os.path.join(latest, "All")
        os.makedirs(all_dir, exist_ok=True)
        return all_dir
    real = ".real_%d" % int(epoch)
    all_dir = os.path.join(root, real, "All")
    os.makedirs(all_dir, exist_ok=True)
    os.symlink(real, latest)
    top_all = os.path.join(root, "All")
    if not os.path.lexists(top_all):
        os.symlink(os.path.join(".latest", "All"), top_all)
    return all_dir


def pkg_package_name(led):
    """Original file name of the ports-mgmt/pkg package in the ledger, or
    None when it is not built."""
    entry = led.get("ports", {}).get("ports-mgmt/pkg")
    if not entry or entry.get("state") != "built" or not entry.get("pkgfile"):
        return None
    return entry.get("pkgfile_orig") or entry["pkgfile"]


def link_latest_pkg(led, all_dir):
    """poudriere's ensure_pkg_installed (common.sh) bootstraps pkg into
    the jail from packages/Latest/pkg.pkg and, when that file is missing,
    runs delete_all_pkgs ("pkg bootstrap missing: unable to inspect
    existing packages") -- every seeded package is thrown away (measured
    2026-09-02, run 33624401320). Recreate the symlink poudriere itself
    leaves behind: Latest/pkg.pkg -> ../All/pkg-<version>.pkg. Returns
    the link target, or None when pkg is not among the seeded files."""
    name = pkg_package_name(led)
    if not name or not os.path.isfile(os.path.join(all_dir, name)):
        return None
    latest_dir = os.path.join(os.path.dirname(all_dir), "Latest")
    os.makedirs(latest_dir, exist_ok=True)
    link = os.path.join(latest_dir, "pkg.pkg")
    target = os.path.join("..", "All", name)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(target, link)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--existing", required=True,
                        help="directory holding one <shard-tag>/ subdir per "
                             "downloaded shard")
    parser.add_argument("--dest", required=True,
                        help="poudriere's package root for the jail, "
                             ".../packages/<jail>-<tree>")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.ledger):
        print("seed: no ledger; nothing to seed")
        return 0
    with open(args.ledger) as handle:
        led = json.load(handle)

    dirs = []
    if os.path.isdir(args.existing):
        for name in sorted(os.listdir(args.existing)):
            path = os.path.join(args.existing, name)
            if os.path.isdir(path):
                dirs.append(path)

    pairs = seed_plan(led, dirs)
    all_dir = seed_layout(args.dest, time.time())
    for source, original in pairs:
        shutil.copyfile(source, os.path.join(all_dir, original))
    latest = link_latest_pkg(led, all_dir)
    print("seed: placed %d published packages into %s" % (len(pairs), all_dir))
    if latest:
        print("seed: Latest/pkg.pkg -> %s" % latest)
    else:
        print("seed: WARNING: no ports-mgmt/pkg package seeded; poudriere "
              "will discard every seeded package (pkg bootstrap missing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
