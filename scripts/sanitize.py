"""Map FreeBSD package filenames to GitHub-safe release asset names.

FreeBSD 15 names every package <name>-<version>~<epoch>$<hash>.pkg. A census
of the 38169 packages in FreeBSD:15:amd64 found '~' and '$' in all of them,
',' in 1111 and '+' in 125. GitHub renames release assets that contain
special characters, by rules it does not document, so the rename is done
here instead and the result is written into the packagesite repopath. pkg
validates the sha256 recorded in the manifest, never the filename, so
renaming a package file is safe.
"""

import string

ALLOWED = frozenset(string.ascii_letters + string.digits + "._-")


def sanitize_asset_name(name):
    """Replace every character outside [A-Za-z0-9._-] with '-'."""
    return "".join(c if c in ALLOWED else "-" for c in name)


def sanitize_all(names):
    """Return {original_name: safe_name} for every name given.

    Raises ValueError if two different originals map onto one safe name. A
    collision would make one of the packages permanently unreachable while
    the repository still looked healthy, so it is fatal rather than a
    warning.
    """
    mapping = {}
    owner = {}
    for name in names:
        safe = sanitize_asset_name(name)
        previous = owner.get(safe)
        if previous is not None and previous != name:
            raise ValueError(
                "asset name collision: %r and %r both sanitise to %r"
                % (previous, name, safe))
        owner[safe] = name
        mapping[name] = safe
    return mapping
