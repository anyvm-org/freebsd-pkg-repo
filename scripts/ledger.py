"""The build ledger: the single source of truth for what has been built.

It is published as an asset of the index release. Builder jobs never write
it -- exactly one collector job folds their result manifests in, so parallel
builders cannot race. Every function here is pure: timestamps arrive as
arguments so the module stays testable without freezing the clock.
"""

STATE_PENDING = "pending"
STATE_BUILT = "built"
STATE_FAILED = "failed"
STATE_IGNORED = "ignored"
STATE_OVERSIZE = "oversize"

MAX_FAILURES = 3

LEDGER_VERSION = 1


def _blank_entry():
    return {"state": STATE_PENDING, "pkgfile": None, "shard": None,
            "built_at": None, "fail_count": 0}


def _entry(ports, origin):
    if origin not in ports:
        ports[origin] = _blank_entry()
    return ports[origin]


def new_ledger(abi, ports_commit, origins):
    """Create a ledger with every origin pending."""
    return {
        "version": LEDGER_VERSION,
        "abi": abi,
        "ports_commit": ports_commit,
        "ports": dict((origin, _blank_entry()) for origin in origins),
    }


def add_origins(led, origins):
    """Queue every origin not yet in the ledger as pending. Entries that
    exist keep their state: a built dependency stays built, a listed port
    that already failed keeps its count. Returns the origins added.

    Without this an existing ledger never learned about a new slice: run
    33639294075 built 92 of a 115-port bootstrap slice, and the ledger,
    created for a one-port pilot, reported pending=0 done=True with 18
    ports still to build.
    """
    added = []
    for origin in origins:
        if origin in led["ports"] or has_flavored_entry(led, origin):
            continue
        led["ports"][origin] = _blank_entry()
        added.append(origin)
    return added


def has_flavored_entry(led, origin):
    """True when the ledger holds origin@<flavor> for this bare origin."""
    prefix = origin + "@"
    return any(key.startswith(prefix) for key in led["ports"])


def resolve_flavors(led):
    """Retire bare pending origins that poudriere reported under a FLAVOR.

    A list names devel/git; poudriere builds its default flavor and
    reports the originspec devel/git@default, so merge_result records
    THAT as built while the listed bare origin stays pending forever
    (run 33694090650: every package built, ledger pending=2). A bare
    entry that never got a package and has a flavored sibling is the
    same port, resolved. Returns the origins retired.
    """
    retired = []
    for origin in sorted(led["ports"]):
        entry = led["ports"][origin]
        if "@" in origin or entry.get("pkgfile"):
            continue
        if entry["state"] == STATE_PENDING and has_flavored_entry(led, origin):
            led["ports"].pop(origin)
            retired.append(origin)
    return retired


def merge_result(led, result, now):
    """Fold one builder job's result manifest into the ledger.

    now is an ISO-8601 string supplied by the caller.
    """
    ports = led["ports"]
    for origin, pkgfile in result.get("built", {}).items():
        entry = _entry(ports, origin)
        entry["state"] = STATE_BUILT
        entry["pkgfile"] = pkgfile
        entry["built_at"] = now
        entry["fail_count"] = 0
    for origin in result.get("failed", []):
        entry = _entry(ports, origin)
        entry["fail_count"] += 1
        # A port that already has a published package keeps it. poudriere
        # only rebuilds a built port when it decided the package is stale
        # or could not see it; if that rebuild then fails, the published
        # package is still the best thing to serve and, for ports-mgmt/pkg,
        # the only way the next job can seed anything at all (run
        # 33624401320 demoted pkg after a failure caused by the NFS mount,
        # and the next job then discarded every seeded package as "pkg
        # bootstrap missing"). The failure is counted, not applied.
        if entry["state"] == STATE_BUILT and entry.get("pkgfile"):
            continue
        entry["state"] = (STATE_FAILED
                          if entry["fail_count"] >= MAX_FAILURES
                          else STATE_PENDING)
    for origin in result.get("ignored", []):
        _entry(ports, origin)["state"] = STATE_IGNORED
    for origin in result.get("oversize", {}):
        _entry(ports, origin)["state"] = STATE_OVERSIZE
    return led


def pending_origins(led):
    """Origins still waiting to be built, in a stable order."""
    return sorted(origin for origin, entry in led["ports"].items()
                  if entry["state"] == STATE_PENDING)


def is_done(led):
    return not pending_origins(led)


def retarget(led, ports_commit):
    """Point the ledger at a new ports tree.

    Ports that exhausted their retries get another chance, since the new
    tree may have fixed them. Built packages are left alone; whether to
    rebuild one is a version comparison made elsewhere.
    """
    led["ports_commit"] = ports_commit
    for entry in led["ports"].values():
        if entry["state"] == STATE_FAILED:
            entry["state"] = STATE_PENDING
            entry["fail_count"] = 0
    return led
