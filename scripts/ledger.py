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
MAX_INTERRUPTIONS = 2

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


def canonical_origin(listed, originspec):
    """The ledger key for an originspec poudriere reported.

    The origin list follows poudriere's own convention (bulk -a writes it
    that way): a port's DEFAULT flavor is listed bare (devel/git,
    devel/llvm20) and its other flavors with @name (devel/git@lite,
    devel/llvm20@lite). poudriere reports the default flavor with its
    real name, devel/git@default or devel/py-foo@py312, which is not what
    the list says. So: a reported origin@flavor that is NOT itself listed
    while its bare origin IS listed is the default flavor and keys as the
    bare origin. Everything else keys as reported.
    """
    if "@" in originspec and originspec not in listed:
        bare = originspec.split("@", 1)[0]
        if bare in listed:
            return bare
    return originspec


def canonicalise(led, listed):
    """Re-key entries recorded under a default flavor's real name onto the
    listed bare origin (run 33694090650 stored devel/git@default and
    security/sudo@default next to pending devel/git and security/sudo).
    A bare entry that has nothing recorded yields to the flavored one.
    Returns [(old_key, new_key)]."""
    listed = set(listed)
    moved = []
    for key in sorted(led["ports"]):
        target = canonical_origin(listed, key)
        if target == key:
            continue
        entry = led["ports"].pop(key)
        current = led["ports"].get(target)
        if current is None or not current.get("pkgfile"):
            led["ports"][target] = entry
        moved.append((key, target))
    return moved


def add_origins(led, origins):
    """Queue every listed origin not yet in the ledger as pending. Entries
    that exist keep their state: a built dependency stays built, a listed
    port that already failed keeps its count. Returns the origins added.

    Without this an existing ledger never learned about a new slice: run
    33639294075 built 92 of a 115-port bootstrap slice, and the ledger,
    created for a one-port pilot, reported pending=0 done=True with 18
    ports still to build.
    """
    added = []
    for origin in origins:
        if origin in led["ports"]:
            continue
        led["ports"][origin] = _blank_entry()
        added.append(origin)
    return added


def merge_result(led, result, now, listed=None):
    """Fold one builder job's result manifest into the ledger.

    now is an ISO-8601 string supplied by the caller. listed, when given,
    is the origin list the round was cut from; reported originspecs are
    keyed through canonical_origin against it.
    """
    listed = set(listed or ())

    def key(originspec):
        return canonical_origin(listed, originspec)

    ports = led["ports"]
    for origin, pkgfile in result.get("built", {}).items():
        entry = _entry(ports, key(origin))
        entry["state"] = STATE_BUILT
        entry["pkgfile"] = pkgfile
        entry["built_at"] = now
        entry["fail_count"] = 0
    for origin in result.get("failed", []):
        entry = _entry(ports, key(origin))
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
        _entry(ports, key(origin))["state"] = STATE_IGNORED
    for origin in result.get("oversize", {}):
        _entry(ports, key(origin))["state"] = STATE_OVERSIZE
    # A build the job's deadline cut short. Once may be bad luck (the
    # port started late in the job); twice means it does not fit the
    # machine, and retrying would burn a whole job every round.
    for origin in result.get("interrupted", []):
        entry = _entry(ports, key(origin))
        if entry["state"] != STATE_PENDING:
            continue
        entry["interrupt_count"] = entry.get("interrupt_count", 0) + 1
        if entry["interrupt_count"] >= MAX_INTERRUPTIONS:
            entry["state"] = STATE_OVERSIZE
    return led


def mark_ignored(led, origins):
    """Record every listed origin (all flavors of it) as ignored: the CI
    blacklist. Built entries are left alone. Returns the keys changed."""
    bare = set(o.split("@", 1)[0] for o in origins)
    changed = []
    for key, entry in led["ports"].items():
        if key.split("@", 1)[0] in bare and entry["state"] == STATE_PENDING:
            entry["state"] = STATE_IGNORED
            changed.append(key)
    return sorted(changed)


def requeue(led, states):
    """Put every entry in one of the given states back to pending with a
    clean failure count: after a fixup for a port that kept failing, or
    when a bigger machine takes over the oversize set. Returns the keys
    requeued."""
    changed = []
    for key, entry in led["ports"].items():
        if entry["state"] in states:
            entry["state"] = STATE_PENDING
            entry["fail_count"] = 0
            entry["interrupt_count"] = 0
            changed.append(key)
    return sorted(changed)


def parse_rebuild_requests(text):
    """config/rebuild: one "origin tag" per line, '#' comments. The tag
    names the fix the rebuild is for; changing it asks for another
    rebuild, keeping it means the request has been served."""
    requests = {}
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2:
            requests[fields[0]] = fields[1]
    return requests


def requeue_tagged(led, requests):
    """Put the listed origins (every flavor of them) back to pending
    whatever their state, once per tag: a port whose published package
    is wrong (devel/icu's manifest missed libicudata.so.76) or that
    failed for a reason since fixed. An entry that already carries a
    published package keeps pkgfile and shard and is flagged "rebuild",
    so the merge replaces the asset instead of skipping the rebuilt
    file as a duplicate. Returns the keys requeued."""
    changed = []
    for key, entry in led["ports"].items():
        tag = requests.get(key.split("@", 1)[0])
        if tag is None or entry.get("rebuild_tag") == tag:
            continue
        entry["state"] = STATE_PENDING
        entry["fail_count"] = 0
        entry["interrupt_count"] = 0
        entry["rebuild_tag"] = tag
        if entry.get("pkgfile") and entry.get("shard") is not None:
            entry["rebuild"] = True
        changed.append(key)
    return sorted(changed)


def note_merged_run(led, run_id):
    """Record that a Build run's results went into this ledger, and
    refuse a second time: re-merging a run whose merge already published
    counts every failure and interruption in it twice (the merge-only run
    of 33948710908 pushed four ports to oversize and one to failed that
    way). Raises ValueError on a repeat."""
    done = led.setdefault("merged_runs", [])
    run_id = str(run_id)
    if run_id in done:
        raise ValueError("run %s was already merged into this ledger; "
                         "re-merging would count its failures twice" % run_id)
    done.append(run_id)


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
