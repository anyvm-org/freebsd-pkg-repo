"""Rewrite packagesite.yaml so every package points at its release shard.

pkg builds a download URL by pasting repopath onto the repository URL
verbatim (freebsd/pkg, libpkg/repo/binary/fetch.c):

    pkg_snprintf(url, sizeof(url), "%S/%R", packagesite, pkg);

The repository URL is the index release,
.../releases/download/idx-FreeBSD-15-riscv64, while packages live under
other release tags, so repopath climbs one level:

    ../pkg-FreeBSD-15-riscv64-007/bash-5.2.37-2-abcdefgh.pkg

Two facts make that safe, both checked on 2026-09-01. GitHub's own server
normalises the '..' -- a request carrying a literal '..' (curl
--path-as-is) still returned HTTP 206. And in the install path pkg derives
the local cache filename from name-version-checksum, not from repopath, so
the '..' cannot escape /var/cache/pkg.

packagesite.yaml is JSON, one object per line, despite the name.
"""

import json
import posixpath


def rewrite_line(line, shard_tag_of, safe_name_of):
    """Rewrite one packagesite.yaml entry.

    Raises KeyError if the package is not in the maps -- a package with no
    shard must never be published, because its entry would advertise a
    download that does not exist.
    """
    entry = json.loads(line)
    original = posixpath.basename(entry["repopath"])
    safe = safe_name_of[original]
    entry["repopath"] = "../%s/%s" % (shard_tag_of[safe], safe)
    return json.dumps(entry, sort_keys=True)


def rewrite_stream(lines, shard_tag_of, safe_name_of):
    """Rewrite every non-blank line of a packagesite.yaml."""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        yield rewrite_line(line, shard_tag_of, safe_name_of)
