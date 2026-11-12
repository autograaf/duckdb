import os
import re
import struct
import sys

AR_MAGIC = b'!<arch>\n'
AR_HEADER_SIZE = 60
SECTION_HEADER_SIZE = 40
BIGOBJ_HEADER_SIZE = 56
# /GL objects share the anonymous header but not this class id
BIGOBJ_CLASS_ID = bytes.fromhex('c7a1bad1eebaa94baf20faf66aa4dcb8')
# IMAGE_FILE_MACHINE_ I386, AMD64, ARM64, ARM64EC, ARM64X
COFF_MACHINES = (0x14C, 0x8664, 0xAA64, 0xA641, 0xA64E)
EXPORT_DIRECTIVE = re.compile(rb'"[/-]export:[^"]*"|(?<!\S)[/-]export:(?:"[^"]*"|[^\s"])+', re.IGNORECASE)


def section_table(data, start):
    sig1, sig2 = struct.unpack_from('<HH', data, start)
    if sig1 in COFF_MACHINES:
        count = struct.unpack_from('<H', data, start + 2)[0]
        optional_header_size = struct.unpack_from('<H', data, start + 16)[0]
        return start + 20 + optional_header_size, count
    version, machine = struct.unpack_from('<HH', data, start + 4)
    is_bigobj = data[start + 12 : start + 28] == BIGOBJ_CLASS_ID
    if sig1 == 0 and sig2 == 0xFFFF and version >= 2 and machine in COFF_MACHINES and is_bigobj:
        return start + BIGOBJ_HEADER_SIZE, struct.unpack_from('<I', data, start + 44)[0]
    if sig1 == 0 and sig2 == 0xFFFF and version == 0:
        # an import object, which has no sections
        return start, 0
    return None, 0


def strip_member(data, start, size):
    if size < BIGOBJ_HEADER_SIZE:
        return 0
    table, count = section_table(data, start)
    if table is None:
        return None
    stripped = 0
    for i in range(count):
        header = table + i * SECTION_HEADER_SIZE
        if data[header : header + 8] != b'.drectve':
            continue
        raw_size, raw_offset = struct.unpack_from('<II', data, header + 16)
        begin = start + raw_offset
        directives = data[begin : begin + raw_size]
        # same-length blanks keep every offset in the object and the archive index valid
        blanked, n = EXPORT_DIRECTIVE.subn(lambda m: b' ' * len(m.group()), directives)
        data[begin : begin + raw_size] = blanked
        stripped += n
    return stripped


def strip_archive(data):
    if data[: len(AR_MAGIC)] != AR_MAGIC:
        raise ValueError('not an archive')
    stripped = 0
    unreadable = 0
    offset = len(AR_MAGIC)
    while offset + AR_HEADER_SIZE <= len(data):
        name = data[offset : offset + 16]
        size = int(data[offset + 48 : offset + 58])
        start = offset + AR_HEADER_SIZE
        # "/" and "//" are the symbol index and the long-name table
        if name.rstrip() not in (b'/', b'//'):
            member = strip_member(data, start, size)
            if member is None:
                unreadable += 1
            else:
                stripped += member
        offset = start + size + (size & 1)
    return stripped, unreadable


def main():
    source, target = sys.argv[1:3]
    with open(source, 'rb') as f:
        data = bytearray(f.read())
    stripped, unreadable = strip_archive(data)
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, 'wb') as f:
        f.write(data)
    print(f'{target}: blanked {stripped} export directives')
    if unreadable:
        # LTO bitcode keeps its directives where this script cannot reach them
        print(f'{target}: {unreadable} members are not COFF objects and keep their exports', file=sys.stderr)


if __name__ == '__main__':
    main()
