"""Merge the result manifests of the parallel build jobs into one.

Each matrix job builds one slice and its dependency closure, so two jobs
may build the same dependency in one round; the first artifact wins and
the other copy is dropped. A port counts as failed only when no job
built it (one job's failure may be another job's success when the
failure was environmental). Ignored and oversize entries are unioned.

Usage:
  merge.py --jobs work/jobs --out work/merged
where work/jobs/<name>/result.json and work/jobs/<name>/pkgs/*.pkg come
from the build-* artifacts. Writes work/merged/result.json and copies
the winning package files into work/merged/pkgs/.
"""

import argparse
import json
import os
import shutil
import sys


def merge_results(results):
    """results: [(name, result_dict)] in a stable order. Returns
    (merged_result, {pkgfile: name_of_job_that_supplies_it})."""
    built = {}
    supplier = {}
    failed = set()
    ignored = set()
    oversize = {}
    for name, result in results:
        for origin, pkgfile in sorted(result.get("built", {}).items()):
            if origin in built:
                continue
            built[origin] = pkgfile
            if pkgfile not in supplier:
                supplier[pkgfile] = name
        failed.update(result.get("failed", []))
        ignored.update(result.get("ignored", []))
        oversize.update(result.get("oversize", {}))
    failed = sorted(o for o in failed if o not in built)
    ignored = sorted(o for o in ignored if o not in built)
    merged = {"built": built, "failed": failed, "ignored": ignored,
              "oversize": oversize}
    return merged, supplier


def load_jobs(jobs_dir):
    results = []
    for name in sorted(os.listdir(jobs_dir)):
        path = os.path.join(jobs_dir, name, "result.json")
        if os.path.isfile(path):
            with open(path) as handle:
                results.append((name, json.load(handle)))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    results = load_jobs(args.jobs)
    merged, supplier = merge_results(results)
    pkgs_out = os.path.join(args.out, "pkgs")
    os.makedirs(pkgs_out, exist_ok=True)
    missing = []
    for pkgfile, name in sorted(supplier.items()):
        source = os.path.join(args.jobs, name, "pkgs", pkgfile)
        if os.path.isfile(source):
            shutil.copyfile(source, os.path.join(pkgs_out, pkgfile))
        else:
            missing.append((name, pkgfile))
    with open(os.path.join(args.out, "result.json"), "w") as handle:
        json.dump(merged, handle, indent=2, sort_keys=True)
    print("merge: %d jobs, built=%d failed=%d ignored=%d, %d package files"
          % (len(results), len(merged["built"]), len(merged["failed"]),
             len(merged["ignored"]), len(supplier) - len(missing)))
    for name, pkgfile in missing:
        print("merge: WARNING: %s reported %s built but shipped no file"
              % (name, pkgfile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
