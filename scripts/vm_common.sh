# Sourced by vm_prepare.sh and vm_build.sh inside the FreeBSD build VM.
# POSIX sh. Defines helpers only; sets nothing on its own.

# ensure_binmisc <target_arch>
#
# poudriere resolves the emulator with "binmiscctl lookup ${wanted_arch#*.}"
# (src/share/poudriere/common.sh), so the activator must be named exactly
# after TARGET_ARCH. binmisc entries live in the kernel, not on disk: a
# cached, snapshot-booted image comes up without them, which is why this
# runs in BOTH prepare and run. The rc script from emulators/qemu-user-static
# registers every architecture with the right magic; the hand-written
# fallback below knows riscv64 only and refuses any other target rather
# than register a wrong magic.
ensure_binmisc() {
    target_arch="$1"
    if binmiscctl lookup "${target_arch}" >/dev/null 2>&1; then
        return 0
    fi
    service qemu_user_static onestart >/dev/null 2>&1 || true
    if binmiscctl lookup "${target_arch}" >/dev/null 2>&1; then
        return 0
    fi
    if [ "${target_arch}" != "riscv64" ]; then
        echo "FATAL: service qemu_user_static did not register ${target_arch}," >&2
        echo "       and the manual fallback only knows riscv64" >&2
        binmiscctl list >&2 || true
        return 1
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
    binmiscctl add "${target_arch}" \
        --interpreter "/usr/local/bin/qemu-${target_arch}-static" \
        --magic '\x7f\x45\x4c\x46\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xf3\x00' \
        --mask '\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff' \
        --size 20 --set-enabled --pre-open
    binmiscctl lookup "${target_arch}" >/dev/null 2>&1 || {
        echo "FATAL: no binmiscctl activator named ${target_arch}" >&2
        return 1
    }
}

# jail_exists <name>
#
# Compare NAMES, never exit codes: "poudriere jail -l" returns 0 even when
# no jails directory exists (jail.sh:108) and -j does not filter the
# listing. -q drops the header, -n prints only the name.
jail_exists() {
    poudriere jail -l -q -n 2>/dev/null | grep -qx "$1"
}

# ports_tree_exists <name>
ports_tree_exists() {
    poudriere ports -l 2>/dev/null | awk '{print $1}' | grep -qx "$1"
}
