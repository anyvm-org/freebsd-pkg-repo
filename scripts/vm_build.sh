#!/bin/sh
# Runs INSIDE the FreeBSD amd64 build VM. Registers qemu-user emulation for
# riscv64, creates a poudriere jail from the official riscv64 release sets,
# builds one slice of ports, and writes a result manifest.
set -eu

: "${ABI_SLUG:?ABI_SLUG is required}"
: "${FBSD_VERSION:?FBSD_VERSION is required}"
: "${POUDRIERE_ARCH:?POUDRIERE_ARCH is required}"
: "${JAIL:?JAIL is required}"
: "${SLICE_FILE:?SLICE_FILE is required}"
BUILD_DEADLINE="${BUILD_DEADLINE:-16200}"
PORTS_TREE="${PORTS_TREE:-default}"

TARGET="${POUDRIERE_ARCH%.*}"        # riscv
TARGET_ARCH="${POUDRIERE_ARCH#*.}"   # riscv64

OUTDIR="$(dirname "${SLICE_FILE}")/out"
mkdir -p "${OUTDIR}"

echo "=== host ==="
uname -a
df -h /

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
cat >> /usr/local/etc/poudriere.conf <<'CONF'
NO_ZFS=yes
BASEFS=/usr/local/poudriere
DISTFILES_CACHE=/usr/ports/distfiles
ALLOW_MAKE_JOBS=yes
QEMU_MAX_EXECUTION_TIME=7200
QEMU_NOHANG_TIME=1800
CONF

# ---------------------------------------------------------------------
# Jail from the official riscv64 release sets. download.freebsd.org and
# archive.freebsd.org are NOT sync-locked, so probe and fall back rather
# than hardcoding one host.
# ---------------------------------------------------------------------
BASE_URL="https://download.freebsd.org/releases/${TARGET}/${TARGET_ARCH}/${FBSD_VERSION}"
if ! fetch -qo /dev/null "${BASE_URL}/base.txz"; then
    BASE_URL="https://archive.freebsd.org/old-releases/${TARGET}/${TARGET_ARCH}/${FBSD_VERSION}"
    fetch -qo /dev/null "${BASE_URL}/base.txz" || {
        echo "FATAL: no base.txz for ${FBSD_VERSION} ${TARGET_ARCH}" >&2
        exit 1
    }
fi
echo "using base sets from ${BASE_URL}"

if ! poudriere jail -l -j "${JAIL}" >/dev/null 2>&1; then
    # -X: do not build native-xtools; there is no src tree in this VM.
    poudriere jail -c -j "${JAIL}" -v "${FBSD_VERSION}" \
        -a "${POUDRIERE_ARCH}" -m "url=${BASE_URL}" -X
fi

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
( sleep "${BUILD_DEADLINE}"; kill -TERM "${BULK_PID}" 2>/dev/null ) &
WATCHDOG_PID=$!
wait "${BULK_PID}"
BULK_RC=$?
kill "${WATCHDOG_PID}" 2>/dev/null
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

echo "=== pkg repo artefact layout (Task 6 step 1) ==="
if [ -d "${PKGDIR}" ]; then
    pkg repo "${PKGDIR}" || echo "pkg repo failed"
    ls -la "${PKGDIR}"
    for f in meta.conf packagesite.pkg data.pkg; do
        if [ -f "${PKGDIR}/${f}" ]; then
            echo "--- ${f} ---"
            case "${f}" in
                meta.conf) cat "${PKGDIR}/${f}" ;;
                *)         tar -tvf "${PKGDIR}/${f}" ;;
            esac
        fi
    done
    cp -f "${PKGDIR}/meta.conf" "${OUTDIR}/" 2>/dev/null || true
    if [ -f "${PKGDIR}/packagesite.pkg" ]; then
        mkdir -p "${OUTDIR}/site"
        tar -xf "${PKGDIR}/packagesite.pkg" -C "${OUTDIR}/site"
        ls -la "${OUTDIR}/site"
        head -c 600 "${OUTDIR}/site/packagesite.yaml" 2>/dev/null || true
        echo
    fi
fi

df -h /
echo "vm_build.sh done"
