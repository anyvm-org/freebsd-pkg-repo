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

# devel/icu: the port links the libicudata stub with -nostdlib (its
# files/patch-config_mh-bsd-gcc sets LDFLAGSICUDT=-nodefaultlibs
# -nostdlib), so no startup object goes in and the library has neither a
# .note.tag section nor an ELF OS/ABI byte; on amd64 and aarch64 the
# linker still stamps OS/ABI FreeBSD, on riscv64 it stays SYSV. pkg then
# does not know which OS the file is for and leaves libicudata.so.76 out
# of shlibs_provided, and every package that needs it fails to install
# ("Missing shlib libicudata.so.76 required by boost-libs", round 10,
# 2026-09-05). Keeping -nodefaultlibs but dropping -nostdlib links the
# usual crt objects, which carry the note.
f="${PORTSDIR}/devel/icu/files/patch-config_mh-bsd-gcc"
if [ -f "$f" ] && grep -q '^+LDFLAGSICUDT=-nodefaultlibs -nostdlib$' "$f"; then
    sed -i '' 's/^+LDFLAGSICUDT=-nodefaultlibs -nostdlib$/+LDFLAGSICUDT=-nodefaultlibs/' "$f"
    echo "fixup: devel/icu libicudata linked with the crt objects (.note.tag)"
fi

# devel/orc 0.4.42: orc_riscv_target_init() calls
# orc_riscv_target_get_cpu_flags(), which the file only defines under
# __linux__ (it reads /proc/cpuinfo), so the riscv backend does not
# compile on FreeBSD (round 10, 2026-09-05). The default-flags function
# right above it already guards the call the same way; do so here too.
d="${PORTSDIR}/devel/orc/files"
if [ -d "${PORTSDIR}/devel/orc" ] && [ ! -f "$d/patch-orc_riscv_orcriscvtarget.c" ]; then
    mkdir -p "$d"
    cat > "$d/patch-orc_riscv_orcriscvtarget.c" <<'PATCH'
--- orc/riscv/orcriscvtarget.c.orig	2025-01-01 00:00:00 UTC
+++ orc/riscv/orcriscvtarget.c
@@ -162,7 +162,7 @@ static OrcTarget orc_riscv_target = {
 OrcTarget *
 orc_riscv_target_init (void)
 {
-#ifdef HAVE_RISCV
+#if defined(HAVE_RISCV) && defined(__linux__)
   if (orc_riscv_target_get_cpu_flags () & ORC_TARGET_RISCV_V) {
     ORC_INFO ("This RISC-V CPU supports RVV");
   }
PATCH
    echo "fixup: devel/orc riscv target compiles on FreeBSD"
fi
