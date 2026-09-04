#!/bin/sh
# Edits applied to the ports tree inside the build VM before poudriere
# runs (vm_build.sh: sh config/ports-fixups.sh <portsdir>). Each fixup
# works around a port that cannot build under qemu-user on this
# pipeline; keep every entry idempotent (running twice changes nothing)
# and say why it exists. poudriere notices the modified checkout and
# only records "ports_top_checkout_unclean: yes" in the packages.
set -eu
PORTSDIR="${1:?ports directory}"

# databases/sqlite3: post-install runs "ldd -a" on the freshly built
# binary as a self-check; under bsd-user qemu 3.1 rtld answers "mmap of
# entire address space failed" and the port fails in its stage phase,
# every time, on the 16-core VM as much as on the runner (2026-09-04).
# Prefixing the command with "-" makes make ignore its exit status; the
# package is unaffected, the check was informational.
f="${PORTSDIR}/databases/sqlite3/Makefile"
if [ -f "$f" ] && grep -q '^	${SETENV} LD_LIBMAP_DISABLE=1 ldd -a' "$f"; then
    sed -i '' 's/^	${SETENV} LD_LIBMAP_DISABLE=1 ldd -a/	-${SETENV} LD_LIBMAP_DISABLE=1 ldd -a/' "$f"
    echo "fixup: databases/sqlite3 ldd self-check made non-fatal"
fi
