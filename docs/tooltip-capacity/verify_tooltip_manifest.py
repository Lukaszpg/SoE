#!/usr/bin/env python3
"""Read-only verification of the qualified PD2 S14 ProjectDiablo bytes.

Usage: python verify_tooltip_manifest.py path/to/ProjectDiablo.dll
Requires the adjacent projectdiablo-patch-sites.csv and Python standard library.
Never modifies the DLL. Matching bytes DO NOT mean a safe patch exists.
"""
import csv
import hashlib
import struct
import sys
from pathlib import Path

EXPECTED_SHA256 = "85e20da4d680519381499997f3443194e01691a41202c0e2faf5347c3e1dbebb"
MANIFEST = Path(__file__).with_name("projectdiablo-patch-sites.csv")


def pe_layout(data):
    if data[:2] != b"MZ":
        raise ValueError("Missing MZ header")
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        raise ValueError("Missing PE signature")
    count = struct.unpack_from("<H", data, pe_off + 6)[0]
    optional = pe_off + 24
    if struct.unpack_from("<H", data, optional)[0] != 0x10B:
        raise ValueError("Expected PE32 (not PE32+)")
    base = struct.unpack_from("<I", data, optional + 28)[0]
    table = optional + struct.unpack_from("<H", data, pe_off + 20)[0]
    sections = []
    for i in range(count):
        offset = table + 40 * i
        virtual_size, rva, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        sections.append((rva, min(virtual_size, raw_size), raw_offset))
    return base, sections


def file_offset(va, base, sections):
    rva = va - base
    for start, size, offset in sections:
        if start <= rva < start + size:
            return offset + rva - start
    raise ValueError(f"VA 0x{va:08X} is outside raw-backed sections")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python verify_tooltip_manifest.py ProjectDiablo.dll")
    data = Path(sys.argv[1]).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    base, sections = pe_layout(data)
    print(f"SHA-256: {sha}")
    print(f"Image base: 0x{base:08X}")
    print(f"Expected SHA-256 matches: {sha == EXPECTED_SHA256}")
    checked = mismatches = 0
    with MANIFEST.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            va = int(row["VA"], 16)
            expected = bytes.fromhex(row["original_bytes"])
            pos = file_offset(va, base, sections)
            observed = data[pos:pos + len(expected)]
            correct = observed == expected
            checked += 1
            mismatches += not correct
            print(f"{'PASS' if correct else 'FAIL'} {row['site']}: VA=0x{va:08X}, bytes={observed.hex(' ').upper()}")
    print(f"Verified {checked} sites; mismatches: {mismatches}")
    if sha != EXPECTED_SHA256 or mismatches:
        raise SystemExit("FAILED: Different DLL build; requalify before any patch.")
    print("Baseline matched. Read-only verification; NO DLL changes made.")


if __name__ == "__main__":
    main()
