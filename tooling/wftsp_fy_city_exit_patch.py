#!/usr/bin/env python3
"""Patch the Korean WFTSP FY castle city-exit action.

The Korean release has a corrupt action tail in ``man`` -> ``CITY20.BIN``:
the decoded "나가기" city hotspot points to 0x001D instead of the Taiwan SP
value 0x0B68. At runtime that makes the exit bounce back into FY castle.

This tool edits the decoded 324-byte city record and re-encodes that single
entry using only literal tokens. It then rebuilds the pack table so all later
offsets stay valid. It does not touch any other entry payload.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import struct
from pathlib import Path

from wftsp_dialogue_pointer_patch import PackEntry, parse_pack, rebuild_pack


EXPECTED_ORIGINAL_MAN_SHA256 = (
    "B4B2BBEAC6B7A0716B0F1114D973C0008942D494E36C3996D4D4D83FC08DA76C"
)
EXPECTED_PATCHED_TAIL = 0x0B68
EXPECTED_CORRUPT_TAIL = 0x001D
ENTRY_NAME = "CITY20.BIN"
EXIT_RECORD_INDEX = 4
CITY_HEADER_SIZE = 4
CITY_RECORD_SIZE = 32
CITY_RECORD_COUNT = 10
EXIT_TEXT = "나가기".encode("cp949")


@dataclasses.dataclass(frozen=True)
class PatchReport:
    man_sha256_before: str
    man_sha256_after: str
    changed: bool
    entry_name: str
    entry_index: int
    entry_offset_before: str
    decoded_size: int
    decoded_tail_offset: str
    tail_before: str
    tail_after: str
    entry_size_before: int
    entry_size_after: int
    man_size_before: int
    man_size_after: int
    patch_kind: str


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def decode_man_entry(blob: bytes) -> bytes:
    """Decode the EnCode variant used by the retail resource loader.

    This is not the stage helper's LZSS dialect. Ghidra shows the loader starts
    its 4 KB ring buffer at index 0 with zero-initialized data and reserves low
    nibble 0xF as a run-length token.
    """
    if not blob.startswith(b"EnCode"):
        return blob
    if len(blob) < 15:
        raise ValueError("encoded entry is too short")

    out_size = struct.unpack_from("<I", blob, 6)[0]
    packed_size = struct.unpack_from("<I", blob, 10)[0]
    if packed_size > len(blob):
        raise ValueError(f"packed size exceeds blob length: {packed_size} > {len(blob)}")

    ring = bytearray(4096)
    write_pos = 0
    out = bytearray()
    pos = 15
    flags = blob[14] + 0x100

    def put(value: int) -> None:
        nonlocal write_pos
        out.append(value)
        ring[write_pos] = value
        write_pos = (write_pos + 1) & 0xFFF

    while pos < packed_size:
        if flags & 1:
            put(blob[pos])
            pos += 1
        else:
            if pos + 1 >= packed_size:
                raise ValueError("truncated reference token")
            low = blob[pos]
            high = blob[pos + 1]
            pos += 2
            low_nibble = high & 0x0F
            encoded_offset = low | ((high & 0xF0) << 4)
            if low_nibble == 0x0F:
                if pos >= packed_size:
                    raise ValueError("truncated run-length token")
                value = blob[pos]
                pos += 1
                count = encoded_offset
                for _ in range(count):
                    put(value)
            else:
                source = (encoded_offset + 0x10) & 0xFFF
                for index in range(low_nibble + 3):
                    put(ring[(source + index) & 0xFFF])

        flags >>= 1
        if flags == 1 and pos < packed_size:
            flags = blob[pos] + 0x100
            pos += 1

    if len(out) != out_size:
        raise ValueError(f"decoded size mismatch: got {len(out)}, expected {out_size}")
    return bytes(out)


def encode_man_entry_literals(decoded: bytes) -> bytes:
    """Encode an EnCode entry with literal-only token groups.

    Literal-only output is larger than the retail asset but avoids relying on
    fragile token reuse while still exercising the same loader path.
    """
    comp = bytearray()
    pos = 0
    while pos < len(decoded):
        chunk = decoded[pos : pos + 8]
        comp.append((1 << len(chunk)) - 1)
        comp.extend(chunk)
        pos += len(chunk)
    packed_size = 14 + len(comp)
    return b"EnCode" + struct.pack("<II", len(decoded), packed_size) + bytes(comp)


def find_entry(entries: list[PackEntry], name: str) -> PackEntry:
    for entry in entries:
        if entry.name == name:
            return entry
    raise ValueError(f"entry not found: {name}")


def patch_city20_decoded(decoded: bytes) -> tuple[bytes, dict[str, object]]:
    expected_size = CITY_HEADER_SIZE + CITY_RECORD_COUNT * CITY_RECORD_SIZE
    if len(decoded) != expected_size:
        raise ValueError(f"{ENTRY_NAME}: unexpected decoded size {len(decoded)}")

    record_offset = CITY_HEADER_SIZE + EXIT_RECORD_INDEX * CITY_RECORD_SIZE
    text = decoded[record_offset + 12 : record_offset + 28].split(b"\0", 1)[0]
    if text != EXIT_TEXT:
        raise ValueError(f"{ENTRY_NAME}: exit record text mismatch: {text!r}")

    tail_offset = record_offset + 28
    tail = struct.unpack_from("<I", decoded, tail_offset)[0]
    if tail == EXPECTED_PATCHED_TAIL:
        return decoded, {
            "tail_offset": tail_offset,
            "tail_before": tail,
            "tail_after": tail,
            "patch_kind": "already-fixed",
        }
    if tail != EXPECTED_CORRUPT_TAIL:
        raise ValueError(f"{ENTRY_NAME}: unexpected exit tail 0x{tail:04X}")

    repaired = bytearray(decoded)
    struct.pack_into("<I", repaired, tail_offset, EXPECTED_PATCHED_TAIL)
    return bytes(repaired), {
        "tail_offset": tail_offset,
        "tail_before": tail,
        "tail_after": EXPECTED_PATCHED_TAIL,
        "patch_kind": "decoded-tail-reencode-literal-only",
    }


def patch_man(data: bytes) -> tuple[bytes, PatchReport]:
    entries = parse_pack(data)
    entry = find_entry(entries, ENTRY_NAME)
    decoded = decode_man_entry(entry.blob)
    repaired_decoded, detail = patch_city20_decoded(decoded)

    replacements: dict[int, bytes] = {}
    repaired_blob = entry.blob
    if repaired_decoded != decoded:
        repaired_blob = encode_man_entry_literals(repaired_decoded)
        if decode_man_entry(repaired_blob) != repaired_decoded:
            raise ValueError("encoded CITY20.BIN does not round-trip")
        replacements[entry.index] = repaired_blob

    patched = rebuild_pack(entries, replacements) if replacements else data
    report = PatchReport(
        man_sha256_before=sha256_hex(data),
        man_sha256_after=sha256_hex(patched),
        changed=patched != data,
        entry_name=ENTRY_NAME,
        entry_index=entry.index,
        entry_offset_before=f"0x{entry.offset:X}",
        decoded_size=len(decoded),
        decoded_tail_offset=f"0x{int(detail['tail_offset']):X}",
        tail_before=f"0x{int(detail['tail_before']):04X}",
        tail_after=f"0x{int(detail['tail_after']):04X}",
        entry_size_before=len(entry.blob),
        entry_size_after=len(repaired_blob),
        man_size_before=len(data),
        man_size_after=len(patched),
        patch_kind=str(detail["patch_kind"]),
    )
    return patched, report


def restore_backup(man_path: Path, backup_path: Path) -> None:
    if not backup_path.exists():
        raise FileNotFoundError(f"backup does not exist: {backup_path}")
    shutil.copy2(backup_path, man_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--man", type=Path, default=Path("man"))
    parser.add_argument("--apply", action="store_true", help="write the patched man file")
    parser.add_argument("--restore", action="store_true", help="restore from the backup file")
    parser.add_argument(
        "--backup",
        type=Path,
        default=Path("man.wftsp_fy_city_exit_fix.bak"),
        help="backup path used by --apply and --restore",
    )
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    parser.add_argument("--force", action="store_true", help="allow patching an unknown man hash")
    args = parser.parse_args(argv)

    if args.restore:
        restore_backup(args.man, args.backup)
        print(f"restored {args.man} from {args.backup}")
        return 0

    data = args.man.read_bytes()
    before_hash = sha256_hex(data)
    if before_hash != EXPECTED_ORIGINAL_MAN_SHA256 and not args.force:
        print(
            f"refusing unknown man hash {before_hash}; "
            f"expected {EXPECTED_ORIGINAL_MAN_SHA256}. Use --force after verifying the file.",
            flush=True,
        )
        return 2

    patched, report = patch_man(data)
    report_json = json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2)
    print(report_json)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_json + "\n", encoding="utf-8")

    if args.apply and patched != data:
        if not args.backup.exists():
            shutil.copy2(args.man, args.backup)
        args.man.write_bytes(patched)
        print(f"patched {args.man}")
    elif args.apply:
        print(f"no changes needed for {args.man}")
    else:
        print("dry-run only; pass --apply to write the patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
