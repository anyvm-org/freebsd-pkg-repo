#!/bin/sh
# Runs INSIDE the FreeBSD amd64 build VM. Registers qemu-user emulation for
# riscv64, creates a poudriere jail from the official riscv64 release sets,
# builds one slice of ports, and writes a result manifest.
set -eu

: "${ABI_SLUG:?ABI_SLUG is required}"
: "${FBSD_VERSION:?FBSD_VERSION is required}"
: "${POUDRIERE_ARCH:?POUDRIERE_ARCH is required}"
: "${POUDRIERE_JAIL:?POUDRIERE_JAIL is required}"
: "${SLICE_FILE:?SLICE_FILE is required}"
# Kept as a plain (unexported) shell variable on purpose: an exported JAIL
# makes pkg jexec into that jail for every package script, which is how
# the host's own "pkg install poudriere" failed its POST-INSTALL steps.
JAIL="${POUDRIERE_JAIL}"
BUILD_DEADLINE="${BUILD_DEADLINE:-16200}"
PORTS_TREE="${PORTS_TREE:-default}"

TARGET="${POUDRIERE_ARCH%.*}"        # riscv
TARGET_ARCH="${POUDRIERE_ARCH#*.}"   # riscv64

OUTDIR="$(dirname "${SLICE_FILE}")/out"
mkdir -p "${OUTDIR}"

echo "=== host ==="
uname -a
df -h /

# The catalogue fetch is the first network call in a VM that booted seconds
# ago, so it is retried -- bounded, and fatal when the bound is hit. The ABI
# line exists because one CI run died right here for a different reason:
# build.yml had exported ABI=FreeBSD:15:riscv64 into the VM, pkg reads $ABI
# from the environment, and the amd64 host then asked pkg.freebsd.org for
# riscv64 packages it does not have ("Error updating repositories!",
# 2026-09-02). The variable is TARGET_ABI now; this line makes any repeat
# of that poisoning visible in the first screen of the log.
echo "pkg ABI on the build host: $(pkg config ABI 2>/dev/null || echo unknown)"
attempt=0
until env IGNORE_OSVERSION=yes pkg update -f; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge 5 ]; then
        echo "FATAL: pkg update failed ${attempt} times" >&2
        exit 1
    fi
    echo "pkg update failed (attempt ${attempt}); retrying in 15s" >&2
    sleep 15
done
env IGNORE_OSVERSION=yes pkg install -y poudriere qemu-user-static git python3

# ---------------------------------------------------------------------
# Emulation. poudriere resolves the emulator with
#   binmiscctl lookup ${wanted_arch#*.}
# (src/share/poudriere/common.sh), so the activator must be named exactly
# "riscv64". Prefer the rc script shipped by emulators/qemu-user-static,
# which registers the correct magic for every architecture; fall back to a
# hand-written activator only if that is unavailable.
# ---------------------------------------------------------------------
if ! binmiscctl lookup "${TARGET_ARCH}" >/dev/null 2>&1; then
    service qemu_user_static onestart || true
fi

if ! binmiscctl lookup "${TARGET_ARCH}" >/dev/null 2>&1; then
    # The hand-written fallback below encodes riscv64's ELF magic and the
    # qemu-riscv64-static path only. For any other target the rc script is
    # the sole source of a correct activator (it names the ppc64 binary
    # qemu-ppc64-static, not qemu-powerpc64-static, for instance), so a
    # miss there is fatal rather than papered over with the wrong magic.
    if [ "${TARGET_ARCH}" != "riscv64" ]; then
        echo "FATAL: service qemu_user_static did not register ${TARGET_ARCH}," >&2
        echo "       and the manual fallback only knows riscv64" >&2
        binmiscctl list >&2 || true
        exit 1
    fi
    # magic/mask decode, byte by byte:
    #   00-03 7f 45 4c 46            \x7fELF
    #   04    02                     EI_CLASS   = ELFCLASS64
    #   05    01                     EI_DATA    = ELFDATA2LSB
    #   06    01                     EI_VERSION = 1
    #   07    mask 00                EI_OSABI ignored, so ELFOSABI_NONE and
    #                                ELFOSABI_FREEBSD both match
    #   08-15 00                     EI_ABIVERSION + padding
    #   16-17 02 00, mask fe ff      e_type: matches ET_EXEC(2) and ET_DYN(3)
    #   18-19 f3 00                  e_machine = 0x00f3 = 243 = EM_RISCV
    #                                (Linux include/uapi/linux/elf-em.h)
    binmiscctl add "${TARGET_ARCH}" \
        --interpreter "/usr/local/bin/qemu-${TARGET_ARCH}-static" \
        --magic '\x7f\x45\x4c\x46\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xf3\x00' \
        --mask '\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff' \
        --size 20 --set-enabled --pre-open
fi

# Fail loudly rather than let poudriere claim qemu-user-static is missing.
binmiscctl lookup "${TARGET_ARCH}" || {
    echo "FATAL: no binmiscctl activator named ${TARGET_ARCH}" >&2
    exit 1
}

# ---------------------------------------------------------------------
# poudriere.conf. The defaults are wrong for a 6-hour CI job: when
# QEMU_EMULATING is set poudriere raises MAX_EXECUTION_TIME to
# QEMU_MAX_EXECUTION_TIME (345600 = 4 days) and NOHANG_TIME to
# QEMU_NOHANG_TIME (21600 = 6 hours), both from common.sh:11217-11218.
# A genuinely hung port would then eat the entire job without ever being
# declared hung. Both are pulled inside the job budget here.
# ---------------------------------------------------------------------
mkdir -p /usr/local/etc /usr/ports/distfiles
# NO_SRC=yes drops src.txz from the fetched dist sets (jail.sh:847-850,
# "if [ -z "${SRCPATH}" -a "${NO_SRC:-no}" = "no" ]"). Measured: 241 MB
# and 4m35s per jail creation, for a tree that building ports never reads
# -- native-xtools is already off via "jail -c -X". A full-tree run that
# wants the handful of kmod ports (which need /usr/src) would set this
# back to no; none of them are riscv64-relevant.
cat >> /usr/local/etc/poudriere.conf <<'CONF'
NO_ZFS=yes
NO_SRC=yes
BASEFS=/usr/local/poudriere
DISTFILES_CACHE=/usr/ports/distfiles
ALLOW_MAKE_JOBS=yes
QEMU_MAX_EXECUTION_TIME=7200
QEMU_NOHANG_TIME=1800
CONF

# Per-port make.conf, read by poudriere for every build in every jail.
#
# lang/python3* passes COMPILEALL_OPTS=-j${MAKE_JOBS_NUMBER} to Python's
# own install step; with N > 1 compileall uses multiprocessing, whose
# semaphores misbehave under qemu-user ("semaphore or lock released too
# many times", "EOFError: Ran out of input"). The port ignores that error,
# so the failure only surfaces in the package phase as hundreds of
# "Unable to access file ...__pycache__/*.pyc" and takes every dependent
# port down with it (66 of 115 in the first bootstrap run, 2026-09-02).
# MAKE_JOBS_UNSAFE forces MAKE_JOBS_NUMBER=1 for those ports only, which
# makes compileall single-process. It built cleanly that way.
mkdir -p /usr/local/etc/poudriere.d
cat > /usr/local/etc/poudriere.d/make.conf <<'MK'
.if ${.CURDIR:M*/lang/python3*}
MAKE_JOBS_UNSAFE=yes
.endif
MK

# ---------------------------------------------------------------------
# Jail from the official riscv64 release sets. download.freebsd.org and
# archive.freebsd.org are NOT sync-locked, so probe and fall back rather
# than hardcoding one host.
# ---------------------------------------------------------------------
# "fetch -s" prints the size WITHOUT transferring the body (fetch(1):
# "-s, --print-size  Print the size in bytes of each requested file,
# without fetching it."). The obvious "fetch -o /dev/null" would download
# the whole ~200 MB base.txz just to answer an existence question, twice
# over if the first mirror misses.
BASE_URL="https://download.freebsd.org/releases/${TARGET}/${TARGET_ARCH}/${FBSD_VERSION}"
if ! fetch -qs "${BASE_URL}/base.txz" >/dev/null 2>&1; then
    BASE_URL="https://archive.freebsd.org/old-releases/${TARGET}/${TARGET_ARCH}/${FBSD_VERSION}"
    fetch -qs "${BASE_URL}/base.txz" >/dev/null 2>&1 || {
        echo "FATAL: no base.txz for ${FBSD_VERSION} ${TARGET_ARCH}" >&2
        exit 1
    }
fi
echo "using base sets from ${BASE_URL} ($(fetch -qs "${BASE_URL}/base.txz" 2>/dev/null) bytes)"

# Existence test must compare NAMES, not exit codes: "poudriere jail -l"
# returns 0 even when no jails directory exists at all (jail.sh:108,
# "[ -d ${POUDRIERED}/jails ] || return 0"), and -j does not filter the
# listing. Using the exit code silently skipped jail creation and left
# bulk to fail with "No such jail". -q drops the header, -n prints only
# the name.
if ! poudriere jail -l -q -n 2>/dev/null | grep -qx "${JAIL}"; then
    echo "creating jail ${JAIL} (${POUDRIERE_ARCH}, ${FBSD_VERSION})"
    # -X: do not build native-xtools; there is no src tree in this VM.
    poudriere jail -c -j "${JAIL}" -v "${FBSD_VERSION}" \
        -a "${POUDRIERE_ARCH}" -m "url=${BASE_URL}" -X
else
    echo "jail ${JAIL} already exists"
fi

# Hard assertion. A setup step that is skipped by accident must not be
# discoverable only as a confusing failure three steps later.
poudriere jail -l -q -n 2>/dev/null | grep -qx "${JAIL}" || {
    echo "FATAL: jail ${JAIL} still does not exist after creation" >&2
    poudriere jail -l >&2 || true
    exit 1
}
poudriere jail -l -j "${JAIL}"

if ! poudriere ports -l | awk '{print $1}' | grep -qx "${PORTS_TREE}"; then
    poudriere ports -c -p "${PORTS_TREE}" -m git+https
fi

# Pin the ports tree commit. A full round must build against ONE tree, or
# packages end up compiled against inconsistent dependency versions. The
# collector stores this in the ledger.
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
# finished: one run had "vm_build.sh done" at 15:32:37 and the step ending
# at 20:02:37, exactly BUILD_DEADLINE (16200s) later.
( sleep "${BUILD_DEADLINE}"; kill -TERM "${BULK_PID}" 2>/dev/null ) \
    >/dev/null 2>&1 &
WATCHDOG_PID=$!

wait "${BULK_PID}"
BULK_RC=$?

# Killing the subshell alone leaves its sleep orphaned and still running.
# Reap the children first, then the subshell itself.
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
# anyone can look (lang/python312 "Failed: package", 2026-09-02, took 66
# dependents down with it and no log survived the first time).
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
# Package directory, and an UNSIGNED index so the first CI run reveals
# exactly what "pkg repo" emits. Task 6 of the plan turns this into a
# signed index once the artefact layout is known; printing it here avoids
# a separate investigation VM.
# ---------------------------------------------------------------------
PKGDIR="/usr/local/poudriere/data/packages/${JAIL}-${PORTS_TREE}"
echo "=== packages ==="
ls -la "${PKGDIR}" 2>/dev/null || echo "no package dir at ${PKGDIR}"
find "${PKGDIR}" -name '*.pkg' 2>/dev/null | head -20
find "${PKGDIR}" -name '*.pkg' 2>/dev/null | wc -l

# poudriere ALREADY builds the repository catalogue at the end of a bulk
# run ("Creating pkg repository" / "Packing files for repository"), so
# running "pkg repo" here again is not just redundant, it FAILS: the top
# of PKGDIR is a tree of symlinks into .real_<stamp> (All -> .latest/All),
# which pkg cannot catalogue ("Cannot create repository catalogue"), and
# the failed attempt leaves empty data/packagesite.yaml files behind.
# Take poudriere's own artefacts instead.
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

# packagesite.yaml is what publish.py rewrites: every repopath gets its
# ../<shard-tag>/ prefix before the index is signed and published.
if [ -f "${PKGDIR}/packagesite.pkg" ]; then
    mkdir -p "${OUTDIR}/site"
    tar -xf "${PKGDIR}/packagesite.pkg" -C "${OUTDIR}/site"
    ls -la "${OUTDIR}/site"
    echo "--- first manifest entry ---"
    head -c 400 "${OUTDIR}/site/packagesite.yaml" 2>/dev/null || true
    echo
    echo "--- manifest entries: $(wc -l < "${OUTDIR}/site/packagesite.yaml") ---"
fi

df -h /
echo "vm_build.sh done"
