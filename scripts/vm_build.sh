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

# Packages live on the HOST. PKGDATA is a directory on the NFS-mounted
# runner workspace (kernel NFS: freebsd-vm maps sync=nfs to anyvm's
# sys-nfs on Linux). poudriere has no packages-only location knob --
# POUDRIERE_DATA would drag the .m builder mounts and logs along -- so
# its packages directory becomes a symlink onto the host path. Every
# package poudriere writes, committed or interrupted, is then on the
# runner the moment it exists, and the published packages the runner
# seeded before the VM started are already there for poudriere to
# unqueue ("Using packages from previously failed, or uncommitted,
# build"). The VM image never holds packages.
PKGROOT=/usr/local/poudriere/data/packages
if [ -n "${PKGDATA:-}" ]; then
    mkdir -p "${PKGDATA}/packages" /usr/local/poudriere/data
    if [ -d "${PKGROOT}" ] && [ ! -L "${PKGROOT}" ]; then
        mv "${PKGROOT}" "${PKGROOT}.local.$(date +%s)"
    fi
    ln -sfn "${PKGDATA}/packages" "${PKGROOT}"
    echo "poudriere packages dir -> ${PKGDATA}/packages (host side)"
    # The logs themselves stay in the VM: on the root-squashed export
    # poudriere's chown of its HTML assets under logs/bulk/.html fails
    # with "[ERROR] Unhandled error!" and the dry run dies before it
    # writes the queue (round 7, run 33865909605: no seed anywhere, 260
    # already-published packages rebuilt). What the runner needs when
    # the VM dies mid-job is only the small .poudriere.ports.* status
    # files; they are mirrored to the host every minute during bulk.
    MIRROR="${PKGDATA}/logs/bulk/${JAIL}-${PORTS_TREE}/latest"
    mkdir -p "${MIRROR}"
    echo "poudriere status files mirrored to ${MIRROR} (host side)"
    # The export squashes the guest's root to the runner's uid, so a
    # chown to any other user on that mount is refused. poudriere's
    # package phase chowns its .npkg staging directory (on the package
    # mount) to the build user and the jail then mkdirs into it: as
    # nobody that is "chown: Operation not permitted" followed by
    # "mkdir: /.npkg/All: Permission denied" and every port fails in
    # its package phase (run 33624401320). Building as root keeps every
    # write on that mount coming from the one uid the export allows.
    # Written here, not in the prepared image, so the cached image stays
    # valid; the image is a snapshot, nothing persists.
    # poudriere 3.4.8 still prints "Will build as nobody:nobody" with
    # this set: that line shows PORTBUILD_USER, the per-phase user is
    # chosen separately (BUILD_AS_NON_ROOT = yes && no NEED_ROOT).
    sed -i '' '/^BUILD_AS_NON_ROOT=/d' /usr/local/etc/poudriere.conf
    printf '\nBUILD_AS_NON_ROOT=no\n' >> /usr/local/etc/poudriere.conf
    echo "BUILD_AS_NON_ROOT=no (packages on a root-squashed NFS export)"
    # One phase may use nearly the whole job. The prepared image says
    # QEMU_MAX_EXECUTION_TIME=7200, which on the 4-core runner under
    # emulation fails binutils, icu and geos with "build/timeout" (run
    # 33765900942) -- ports that hundreds of others need. The deadline
    # watchdog still bounds the job; a port that cannot finish inside it
    # is recorded as oversize below, not as a failure.
    PHASE_LIMIT=$(( BUILD_DEADLINE - 600 ))
    [ "${PHASE_LIMIT}" -gt 7200 ] || PHASE_LIMIT=7200
    sed -i '' -e '/^QEMU_MAX_EXECUTION_TIME=/d' -e '/^QEMU_NOHANG_TIME=/d' /usr/local/etc/poudriere.conf
    printf 'QEMU_MAX_EXECUTION_TIME=%s\nQEMU_NOHANG_TIME=%s\n' "${PHASE_LIMIT}" "${PHASE_LIMIT}" >> /usr/local/etc/poudriere.conf
    echo "QEMU_MAX_EXECUTION_TIME=${PHASE_LIMIT} (from BUILD_DEADLINE=${BUILD_DEADLINE})"
    echo "seeded packages: $(ls "${PKGDATA}/packages/${JAIL}-${PORTS_TREE}/.latest/All" 2>/dev/null | wc -l | tr -d ' ')"
fi

# Ports the runner must never attempt (toolchain giants that cannot
# finish inside a 6-hour job under qemu-user). NOT via poudriere's
# blacklist: that trims a flavored port unevenly -- run 33730837363
# logged "Ignoring devel/llvm20@lite: Blacklisted" for the lite flavor
# only, dropped llvm20@default from the queue without marking it
# ignored, and every job died with "Packages stuck in queue (depended
# on but not in queue): llvm20-20.1.8_3". Setting IGNORE in make.conf
# instead makes the ports framework itself declare every flavor of the
# port ignored while poudriere gathers metadata, and dependents are
# then skipped the normal way ("Dependent port ... ignored").
if [ -n "${BLACKLIST:-}" ] && [ -f "${BLACKLIST}" ]; then
    mkdir -p /usr/local/etc/poudriere.d
    {
        echo ""
        echo "# generated by vm_build.sh from config/blacklist"
        grep -v '^[[:space:]]*#' "${BLACKLIST}" | sed -e 's/[[:space:]]*#.*//' -e '/^[[:space:]]*$/d' |
        while read -r origin; do
            echo ".if \${.CURDIR:M*/${origin}}"
            echo "IGNORE=	too large for the CI runner under emulation; built elsewhere"
            echo ".endif"
        done
    } >> /usr/local/etc/poudriere.d/make.conf
    echo "blacklist: $(grep -c '^IGNORE=' /usr/local/etc/poudriere.d/make.conf) origins set IGNORE in poudriere.d/make.conf"
fi

# Per-port workarounds for qemu-user, applied to the (snapshot) ports
# tree; see config/ports-fixups.sh for the list and the reasons.
FIXUPS="$(dirname "$0")/../config/ports-fixups.sh"
if [ -f "${FIXUPS}" ]; then
    sh "${FIXUPS}" "/usr/local/poudriere/ports/${PORTS_TREE}"
fi

# ---------------------------------------------------------------------
# Selective seed. A job must not download the whole repository (tens of
# GB once the tree is in) and poudriere's PACKAGE_FETCH_URL wants one
# repository URL, which a set of shard releases is not. So: dry-run the
# slice first; .poudriere.ports.queued is then the slice plus its whole
# dependency closure; fetch the published part of it (wantlist.py maps
# ledger entries to shard asset URLs) into the committed layout with
# Latest/pkg.pkg; the real bulk unqueues those and builds the rest.
# ---------------------------------------------------------------------
LOGROOT="/usr/local/poudriere/data/logs/bulk/${JAIL}-${PORTS_TREE}"
if [ -n "${PKGDATA:-}" ] && [ -n "${LEDGER:-}" ] && [ -f "${LEDGER}" ] && [ -n "${REPO:-}" ]; then
    SEED_STARTED=$(date +%s)
    echo "=== dry run of the slice for its dependency closure ==="
    poudriere bulk -n -j "${JAIL}" -p "${PORTS_TREE}" -f "${SLICE_FILE}" \
        > "${OUTDIR}/dryrun.log" 2>&1 || echo "dry run exited $? (see dryrun.log)"
    grep -aE 'Queued:|Ignored:|blacklisted' "${OUTDIR}/dryrun.log" | tail -3
    QUEUED="${LOGROOT}/latest/.poudriere.ports.queued"
    if [ -f "${QUEUED}" ]; then
        PYTHONPATH="$(dirname "$0")" python3 "$(dirname "$0")/wantlist.py" \
            --ledger "${LEDGER}" --queued "${QUEUED}" --repo "${REPO}" \
            --abi-slug "${ABI_SLUG}" --out "${OUTDIR}/wantlist.txt"
        SEEDROOT="${PKGDATA}/packages/${JAIL}-${PORTS_TREE}"
        if [ ! -L "${SEEDROOT}/.latest" ]; then
            REAL=".real_$(date +%s)"
            mkdir -p "${SEEDROOT}/${REAL}/All"
            ln -s "${REAL}" "${SEEDROOT}/.latest"
            [ -e "${SEEDROOT}/All" ] || ln -s ".latest/All" "${SEEDROOT}/All"
        fi
        SEEDALL="${SEEDROOT}/.latest/All"
        mkdir -p "${SEEDALL}"
        # Eight fetches at a time; a failed fetch just leaves the port to
        # be rebuilt, so per-file errors are not fatal.
        n=0
        while IFS="$(printf '\t')" read -r url name; do
            [ -n "${url}" ] || continue
            [ -s "${SEEDALL}/${name}" ] && continue
            fetch -q -o "${SEEDALL}/${name}" "${url}" 2>/dev/null || rm -f "${SEEDALL}/${name}" &
            n=$((n + 1))
            [ $((n % 8)) -eq 0 ] && wait
        done < "${OUTDIR}/wantlist.txt"
        wait
        PKGFILE=$(ls "${SEEDALL}" 2>/dev/null | grep -E '^pkg-[0-9].*\.pkg$' | head -1)
        if [ -n "${PKGFILE}" ]; then
            mkdir -p "${SEEDROOT}/.latest/Latest"
            ln -sf "../All/${PKGFILE}" "${SEEDROOT}/.latest/Latest/pkg.pkg"
        else
            echo "WARNING: no pkg-*.pkg among the seeded packages; poudriere will discard the seed (pkg bootstrap missing)"
        fi
        echo "seed: $(ls "${SEEDALL}" | wc -l | tr -d ' ') packages in ${SEEDALL} after $(( $(date +%s) - SEED_STARTED ))s"
    else
        echo "WARNING: dry run left no queue file; building without a seed"
    fi
fi

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

# Mirror the status files to the host while bulk runs (see MIRROR above).
MIRROR_PID=""
if [ -n "${MIRROR:-}" ]; then
    (
        while sleep 60; do
            for f in "${LOGROOT}"/latest/.poudriere.ports.*; do
                [ -f "$f" ] && cp -f "$f" "${MIRROR}/" 2>/dev/null
            done
        done
    ) >/dev/null 2>&1 &
    MIRROR_PID=$!
fi

wait "${BULK_PID}"
BULK_RC=$?

# Killing the subshell alone leaves its sleep orphaned and still running.
pkill -P "${WATCHDOG_PID}" 2>/dev/null
kill "${WATCHDOG_PID}" 2>/dev/null
wait "${WATCHDOG_PID}" 2>/dev/null
if [ -n "${MIRROR_PID}" ]; then
    pkill -P "${MIRROR_PID}" 2>/dev/null
    kill "${MIRROR_PID}" 2>/dev/null
    wait "${MIRROR_PID}" 2>/dev/null
    for f in "${LOGROOT}"/latest/.poudriere.ports.*; do
        [ -f "$f" ] && cp -f "$f" "${MIRROR}/" 2>/dev/null
    done
fi
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

python3 "$(dirname "$0")/result.py" --logdir "${LOGDIR}" --out "${OUTDIR}/result.json"

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
PKGDIR="${PKGROOT}/${JAIL}-${PORTS_TREE}"
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
