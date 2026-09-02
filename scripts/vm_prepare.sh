#!/bin/sh
# Runs INSIDE the FreeBSD amd64 build VM as freebsd-vm's "prepare" step,
# once per cached image: installs poudriere and the emulator, writes the
# poudriere configuration, creates the target jail from the official
# release sets, and clones the ports tree. freebsd-vm then shuts the VM
# down and caches the qcow2 (cache-after-prepare); every later run boots
# that image in snapshot mode and goes straight to vm_build.sh.
#
# Everything here is therefore build-independent and deterministic. The
# ports tree commit is frozen in the image until the prepare text changes
# (see the prepare-epoch line in build.yml), which is exactly the "one
# ports tree per round" rule the design asks for.
set -eu

: "${FBSD_VERSION:?FBSD_VERSION is required}"
: "${POUDRIERE_ARCH:?POUDRIERE_ARCH is required}"
: "${POUDRIERE_JAIL:?POUDRIERE_JAIL is required}"
PORTS_TREE="${PORTS_TREE:-default}"

TARGET="${POUDRIERE_ARCH%.*}"        # riscv
TARGET_ARCH="${POUDRIERE_ARCH#*.}"   # riscv64
# Plain, unexported: an exported JAIL makes service(8) jexec into it.
JAIL="${POUDRIERE_JAIL}"

. "$(dirname "$0")/vm_common.sh"

echo "=== prepare: host ==="
uname -a
echo "pkg ABI on the build host: $(pkg config ABI 2>/dev/null || echo unknown)"

# The catalogue fetch is the first network call in a VM that booted seconds
# ago, so it is retried -- bounded, and fatal when the bound is hit.
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

# Register the emulator now (needed for jail creation below) AND at every
# boot of the cached image: the activator is kernel state, not disk state.
sysrc qemu_user_static_enable=YES >/dev/null
ensure_binmisc "${TARGET_ARCH}"
binmiscctl lookup "${TARGET_ARCH}" | sed -n '1,2p'

# ---------------------------------------------------------------------
# poudriere.conf. The defaults are wrong for a 6-hour CI job: when
# QEMU_EMULATING is set poudriere raises MAX_EXECUTION_TIME to
# QEMU_MAX_EXECUTION_TIME (345600 = 4 days) and NOHANG_TIME to
# QEMU_NOHANG_TIME (21600 = 6 hours), both from common.sh:11217-11218.
# A genuinely hung port would then eat the entire job without ever being
# declared hung. Both are pulled inside the job budget here.
#
# NO_SRC=yes drops src.txz from the fetched dist sets (jail.sh:847-850):
# 241 MB and minutes per jail creation, for a tree that building ports
# never reads -- native-xtools is already off via "jail -c -X".
# ---------------------------------------------------------------------
mkdir -p /usr/local/etc /usr/ports/distfiles /usr/local/etc/poudriere.d
cat > /usr/local/etc/poudriere.conf <<'CONF'
NO_ZFS=yes
NO_SRC=yes
BASEFS=/usr/local/poudriere
DISTFILES_CACHE=/usr/ports/distfiles
ALLOW_MAKE_JOBS=yes
QEMU_MAX_EXECUTION_TIME=7200
QEMU_NOHANG_TIME=1800
CONF

# lang/python3* passes COMPILEALL_OPTS=-j${MAKE_JOBS_NUMBER} to Python's
# own install step; with N > 1 compileall uses multiprocessing, whose
# semaphores misbehave under qemu-user ("semaphore or lock released too
# many times", "EOFError: Ran out of input"). The port ignores that error,
# so it surfaces only in the package phase as hundreds of missing .pyc
# files and takes every dependent port down (66 of 115 on 2026-09-02).
# MAKE_JOBS_UNSAFE forces MAKE_JOBS_NUMBER=1 for those ports only.
cat > /usr/local/etc/poudriere.d/make.conf <<'MK'
.if ${.CURDIR:M*/lang/python3*}
MAKE_JOBS_UNSAFE=yes
.endif
MK

# ---------------------------------------------------------------------
# Jail from the official release sets. download.freebsd.org and
# archive.freebsd.org are NOT sync-locked, so probe and fall back.
# "fetch -s" prints the size WITHOUT transferring the body.
# ---------------------------------------------------------------------
BASE_URL="https://download.freebsd.org/releases/${TARGET}/${TARGET_ARCH}/${FBSD_VERSION}"
if ! fetch -qs "${BASE_URL}/base.txz" >/dev/null 2>&1; then
    BASE_URL="https://archive.freebsd.org/old-releases/${TARGET}/${TARGET_ARCH}/${FBSD_VERSION}"
    fetch -qs "${BASE_URL}/base.txz" >/dev/null 2>&1 || {
        echo "FATAL: no base.txz for ${FBSD_VERSION} ${TARGET_ARCH}" >&2
        exit 1
    }
fi
echo "using base sets from ${BASE_URL} ($(fetch -qs "${BASE_URL}/base.txz" 2>/dev/null) bytes)"

if jail_exists "${JAIL}"; then
    echo "jail ${JAIL} already exists"
else
    echo "creating jail ${JAIL} (${POUDRIERE_ARCH}, ${FBSD_VERSION})"
    # -X: do not build native-xtools; there is no src tree in this VM.
    poudriere jail -c -j "${JAIL}" -v "${FBSD_VERSION}" \
        -a "${POUDRIERE_ARCH}" -m "url=${BASE_URL}" -X
fi
jail_exists "${JAIL}" || {
    echo "FATAL: jail ${JAIL} still does not exist after creation" >&2
    poudriere jail -l >&2 || true
    exit 1
}
poudriere jail -l -j "${JAIL}"

if ! ports_tree_exists "${PORTS_TREE}"; then
    poudriere ports -c -p "${PORTS_TREE}" -m git+https
fi
ports_tree_exists "${PORTS_TREE}" || {
    echo "FATAL: ports tree ${PORTS_TREE} does not exist after creation" >&2
    exit 1
}
echo "ports tree at $(git -C "/usr/local/poudriere/ports/${PORTS_TREE}" rev-parse HEAD 2>/dev/null || echo unknown)"

df -h /
echo "vm_prepare.sh done"
