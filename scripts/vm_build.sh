#!/bin/sh
# Runs INSIDE the FreeBSD amd64 build VM as freebsd-vm's "run" step, on an
# image that vm_prepare.sh already set up (poudriere, emulator, jail,
# ports tree). Builds one slice of ports and writes a result manifest.
# With cache-after-prepare the image boots in snapshot mode, so nothing
# written here persists beyond the job except what goes to OUTDIR on the
# NFS-mounted workspace.
set -eu

: "${ABI_SLUG:?ABI_SLUG is required}"
: "${POUDRIERE_ARCH:?POUDRIERE_ARCH is required}"
: "${POUDRIERE_JAIL:?POUDRIERE_JAIL is required}"
: "${SLICE_FILE:?SLICE_FILE is required}"
BUILD_DEADLINE="${BUILD_DEADLINE:-16200}"
PORTS_TREE="${PORTS_TREE:-default}"

TARGET_ARCH="${POUDRIERE_ARCH#*.}"   # riscv64
# Plain, unexported: an exported JAIL makes service(8) jexec into it.
JAIL="${POUDRIERE_JAIL}"

. "$(dirname "$0")/vm_common.sh"

OUTDIR="$(dirname "${SLICE_FILE}")/out"
mkdir -p "${OUTDIR}"

echo "=== host ==="
uname -a
df -h /
echo "pkg ABI on the build host: $(pkg config ABI 2>/dev/null || echo unknown)"

# The prepared image must carry everything; a miss here means prepare did
# not run or the cache handed back the wrong image. Fail before spending
# hours, and say which half is missing.
command -v poudriere >/dev/null 2>&1 || {
    echo "FATAL: poudriere is not installed; vm_prepare.sh did not run on this image" >&2
    exit 1
}
jail_exists "${JAIL}" || {
    echo "FATAL: jail ${JAIL} is missing; vm_prepare.sh did not run on this image" >&2
    poudriere jail -l >&2 || true
    exit 1
}
ports_tree_exists "${PORTS_TREE}" || {
    echo "FATAL: ports tree ${PORTS_TREE} is missing; vm_prepare.sh did not run on this image" >&2
    exit 1
}
# Kernel state, gone after every reboot of the cached image.
ensure_binmisc "${TARGET_ARCH}"
binmiscctl lookup "${TARGET_ARCH}" | sed -n '1,2p'

# Pin the ports tree commit for the ledger. The tree is frozen inside the
# cached image, so this changes only when the prepare text changes.
if git -C "/usr/local/poudriere/ports/${PORTS_TREE}" rev-parse HEAD \
        > "${OUTDIR}/ports_commit" 2>/dev/null; then
    echo "ports tree at $(cat "${OUTDIR}/ports_commit")"
else
    echo "unknown" > "${OUTDIR}/ports_commit"
    echo "WARNING: could not read the ports tree commit" >&2
fi

# ---------------------------------------------------------------------
# Build the slice under a wall-clock budget. poudriere has no native time
# budget, so it runs in the background with a watchdog. poudriere is
# crash-safe: packages already finished stay on disk, and the interrupted
# port simply stays pending for the next job.
# ---------------------------------------------------------------------
BUILD_STARTED=$(date +%s)
set +e
poudriere bulk -j "${JAIL}" -p "${PORTS_TREE}" -f "${SLICE_FILE}" &
BULK_PID=$!

# The watchdog gets its own stdout/stderr. Without this redirection the
# subshell -- and the sleep inside it -- inherit the script's descriptors,
# and ssh will not close the session while anything still holds them. That
# made every CI job run for the FULL deadline no matter how fast the build
# finished (one run: "vm_build.sh done" at 15:32:37, step end at 20:02:37,
# exactly BUILD_DEADLINE later).
( sleep "${BUILD_DEADLINE}"; kill -TERM "${BULK_PID}" 2>/dev/null ) \
    >/dev/null 2>&1 &
WATCHDOG_PID=$!

wait "${BULK_PID}"
BULK_RC=$?

# Killing the subshell alone leaves its sleep orphaned and still running.
pkill -P "${WATCHDOG_PID}" 2>/dev/null
kill "${WATCHDOG_PID}" 2>/dev/null
wait "${WATCHDOG_PID}" 2>/dev/null
set -e
BUILD_ELAPSED=$(( $(date +%s) - BUILD_STARTED ))
echo "poudriere bulk exited ${BULK_RC} after ${BUILD_ELAPSED}s"
echo "${BUILD_ELAPSED}" > "${OUTDIR}/build_seconds"

# ---------------------------------------------------------------------
# Result manifest. poudriere writes these files into the build log dir
# (common.sh:10260-10267). Field order comes from the badd call sites:
#   ports.built    originspec pkgname elapsed              (common.sh:6908)
#   ports.failed   originspec pkgname phase errortype ...  (common.sh:6935)
#   ports.ignored  originspec pkgname reason               (common.sh:10184)
#   ports.skipped  skipped_originspec skipped_pkgname ...  (common.sh:6651)
# skipped means "a dependency failed", so those ports stay pending rather
# than being recorded as failures of their own.
# ---------------------------------------------------------------------
LOGDIR="/usr/local/poudriere/data/logs/bulk/${JAIL}-${PORTS_TREE}/latest"
echo "=== poudriere log dir ==="
ls -la "${LOGDIR}" || echo "no log dir at ${LOGDIR}"

# bulk failing AND leaving no log directory means it never started a build
# (bad jail, bad ports tree, bad slice file). That must fail the job: the
# alternative is an all-zero result manifest that reads like a clean run
# where nothing happened to be queued.
if [ "${BULK_RC}" -ne 0 ] && [ ! -d "${LOGDIR}" ]; then
    echo "FATAL: poudriere bulk failed (rc=${BULK_RC}) without producing a" >&2
    echo "       build log, so no port was ever attempted." >&2
    exit 1
fi

python3 - "${LOGDIR}" "${OUTDIR}/result.json" <<'PYEOF'
import json
import os
import sys

logdir, out = sys.argv[1], sys.argv[2]


def column(name, index):
    path = os.path.join(logdir, ".poudriere.ports." + name)
    rows = []
    if os.path.exists(path):
        with open(path) as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > index:
                    rows.append((fields[0], fields[index]))
    return rows


built = {}
for origin, pkgname in column("built", 1):
    built[origin] = pkgname + ".pkg"

result = {
    "built": built,
    "failed": [origin for origin, _ in column("failed", 1)],
    "ignored": [origin for origin, _ in column("ignored", 1)],
    "oversize": {},
}
with open(out, "w") as handle:
    json.dump(result, handle, indent=2, sort_keys=True)
print(json.dumps(dict((k, len(v)) for k, v in result.items())))
PYEOF

echo "result manifest written to ${OUTDIR}/result.json"

# Keep the per-port logs of everything that FAILED. poudriere symlinks them
# under logs/errors/; -L copies the real files. Without this a failure on
# CI leaves only the one-line summary, and the runner is gone before
# anyone can look.
if [ -d "${LOGDIR}/logs/errors" ]; then
    mkdir -p "${OUTDIR}/faillogs"
    cp -RL "${LOGDIR}/logs/errors/." "${OUTDIR}/faillogs/" 2>/dev/null || true
    echo "failed-port logs kept: $(ls "${OUTDIR}/faillogs" 2>/dev/null | wc -l | tr -d ' ')"
    for f in "${OUTDIR}"/faillogs/*.log; do
        [ -f "$f" ] || continue
        echo "--- $(basename "$f"): last lines ---"
        tail -n 25 "$f"
    done
fi

# ---------------------------------------------------------------------
# poudriere already built the repository catalogue at the end of bulk
# ("Creating pkg repository"); running "pkg repo" here again is not just
# redundant, it fails on the symlink layout. Take poudriere's artefacts.
# ---------------------------------------------------------------------
PKGDIR="/usr/local/poudriere/data/packages/${JAIL}-${PORTS_TREE}"
echo "=== packages ==="
find "${PKGDIR}" -name '*.pkg' 2>/dev/null | wc -l
echo "=== repository artefacts produced by poudriere ==="
for f in meta.conf packagesite.pkg data.pkg; do
    if [ -f "${PKGDIR}/${f}" ]; then
        echo "--- ${f} ---"
        case "${f}" in
            meta.conf) cat "${PKGDIR}/${f}" ;;
            *)         tar -tvf "${PKGDIR}/${f}" ;;
        esac
        cp -f "${PKGDIR}/${f}" "${OUTDIR}/" 2>/dev/null || true
    else
        echo "MISSING: ${PKGDIR}/${f}"
    fi
done
if [ -f "${PKGDIR}/packagesite.pkg" ]; then
    mkdir -p "${OUTDIR}/site"
    tar -xf "${PKGDIR}/packagesite.pkg" -C "${OUTDIR}/site"
    echo "--- manifest entries: $(wc -l < "${OUTDIR}/site/packagesite.yaml") ---"
fi

df -h /
echo "vm_build.sh done"
