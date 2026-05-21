#!/usr/bin/env python3
"""Patch WFTSP Korean release dialogue bytecode that can soft-lock after Mia's line.

The game stores scenario scripts in the extensionless ``stage`` pack. Entries use
an ``EnCode`` LZSS-like wrapper. This tool keeps every untouched entry byte-for-byte
identical and only re-encodes entries that receive a verified repair.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Iterable


EXPECTED_ORIGINAL_STAGE_SHA256 = (
    "7DEFC5EC01878EF95700EB3460B2065F2FEFCCCE7195068E415C9F5C6C05C401"
)


@dataclasses.dataclass(frozen=True)
class PackEntry:
    index: int
    raw_name: bytes
    offset: int
    blob: bytes

    @property
    def name(self) -> str:
        return self.raw_name.split(b"\0", 1)[0].decode("ascii", errors="ignore")


@dataclasses.dataclass
class Repair:
    entry_name: str
    patch_mode: str
    corrupt_offset: int
    corrupt_run_before: str
    corrupt_run_after: str
    header_offset: int
    header_before: str
    header_after: str
    source_header_offset: int
    source_anchor_offset: int


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def parse_pack(data: bytes) -> list[PackEntry]:
    if len(data) < 4:
        raise ValueError("pack is too short")
    count = struct.unpack_from("<I", data, 0)[0]
    table_end = 4 + count * 17
    if table_end > len(data):
        raise ValueError(f"pack table exceeds file length: count={count}")

    entries: list[PackEntry] = []
    offsets: list[int] = []
    raw_names: list[bytes] = []
    for index in range(count):
        row = 4 + index * 17
        offset = struct.unpack_from("<I", data, row)[0]
        raw_name = data[row + 4 : row + 17]
        offsets.append(offset)
        raw_names.append(raw_name)

    for index, offset in enumerate(offsets):
        next_offset = offsets[index + 1] if index + 1 < len(offsets) else len(data)
        if offset < table_end or offset > len(data) or next_offset < offset:
            raise ValueError(
                f"invalid pack offsets at entry {index}: {offset:#x}->{next_offset:#x}"
            )
        entries.append(PackEntry(index, raw_names[index], offset, data[offset:next_offset]))
    return entries


def decode_entry(blob: bytes) -> bytes:
    if not blob.startswith(b"EnCode"):
        return blob
    if len(blob) < 14:
        raise ValueError("encoded entry is too short")

    out_size = struct.unpack_from("<I", blob, 6)[0]
    comp = blob[14:]
    window_size = 4096
    lookahead = 18
    window = bytearray(b" " * (window_size - lookahead) + b"\0" * lookahead)
    write_pos = window_size - lookahead
    out = bytearray()
    pos = 0

    while len(out) < out_size and pos < len(comp):
        flags = comp[pos]
        pos += 1
        for _ in range(8):
            if flags & 1:
                if pos >= len(comp):
                    break
                value = comp[pos]
                pos += 1
                out.append(value)
                window[write_pos] = value
                write_pos = (write_pos + 1) & (window_size - 1)
            else:
                if pos + 1 >= len(comp):
                    break
                low = comp[pos]
                high = comp[pos + 1]
                pos += 2
                ref_offset = low | ((high & 0xF0) << 4)
                ref_length = (high & 0x0F) + 3
                for i in range(ref_length):
                    value = window[(ref_offset + i) & (window_size - 1)]
                    out.append(value)
                    window[write_pos] = value
                    write_pos = (write_pos + 1) & (window_size - 1)
                    if len(out) >= out_size:
                        break
            flags >>= 1
            if len(out) >= out_size:
                break

    if len(out) != out_size:
        raise ValueError(f"decoded size mismatch: got {len(out)}, expected {out_size}")
    return bytes(out)


def map_decoded_literals(blob: bytes) -> list[int | None]:
    """Return compressed-byte positions for decoded literals, or None for refs."""
    if not blob.startswith(b"EnCode"):
        return [i for i in range(len(blob))]
    if len(blob) < 14:
        raise ValueError("encoded entry is too short")

    out_size = struct.unpack_from("<I", blob, 6)[0]
    comp = blob[14:]
    window_size = 4096
    lookahead = 18
    window = bytearray(b" " * (window_size - lookahead) + b"\0" * lookahead)
    write_pos = window_size - lookahead
    literal_map: list[int | None] = []
    pos = 0

    while len(literal_map) < out_size and pos < len(comp):
        flags = comp[pos]
        pos += 1
        for _ in range(8):
            if flags & 1:
                if pos >= len(comp):
                    break
                value = comp[pos]
                comp_pos = 14 + pos
                pos += 1
                literal_map.append(comp_pos)
                window[write_pos] = value
                write_pos = (write_pos + 1) & (window_size - 1)
            else:
                if pos + 1 >= len(comp):
                    break
                low = comp[pos]
                high = comp[pos + 1]
                pos += 2
                ref_offset = low | ((high & 0xF0) << 4)
                ref_length = (high & 0x0F) + 3
                for i in range(ref_length):
                    value = window[(ref_offset + i) & (window_size - 1)]
                    literal_map.append(None)
                    window[write_pos] = value
                    write_pos = (write_pos + 1) & (window_size - 1)
                    if len(literal_map) >= out_size:
                        break
            flags >>= 1
            if len(literal_map) >= out_size:
                break

    if len(literal_map) != out_size:
        raise ValueError(f"literal map size mismatch: got {len(literal_map)}, expected {out_size}")
    return literal_map


def encode_entry(decoded: bytes) -> bytes:
    """Encode with the same 4 KB LZSS token layout used by the stage pack."""
    window_size = 4096
    lookahead = 18
    min_match = 3
    window = bytearray(b" " * (window_size - lookahead) + b"\0" * lookahead)
    write_pos = window_size - lookahead
    comp = bytearray()

    pos = 0
    while pos < len(decoded):
        flag_pos = len(comp)
        comp.append(0)
        flags = 0
        tokens = bytearray()

        for bit in range(8):
            if pos >= len(decoded):
                break

            best_offset = 0
            best_length = 0
            max_length = min(lookahead, len(decoded) - pos)
            if max_length >= min_match:
                for candidate in range(window_size):
                    distance = (write_pos - candidate) & (window_size - 1)
                    if distance == 0:
                        continue
                    limit = min(max_length, distance)
                    if limit < min_match:
                        continue
                    length = 0
                    while (
                        length < limit
                        and window[(candidate + length) & (window_size - 1)]
                        == decoded[pos + length]
                    ):
                        length += 1
                    if length > best_length:
                        best_offset = candidate
                        best_length = length
                        if best_length == max_length:
                            break

            if best_length >= min_match:
                tokens.append(best_offset & 0xFF)
                tokens.append(((best_offset >> 4) & 0xF0) | (best_length - min_match))
                for value in decoded[pos : pos + best_length]:
                    window[write_pos] = value
                    write_pos = (write_pos + 1) & (window_size - 1)
                pos += best_length
            else:
                value = decoded[pos]
                flags |= 1 << bit
                tokens.append(value)
                window[write_pos] = value
                write_pos = (write_pos + 1) & (window_size - 1)
                pos += 1

        comp[flag_pos] = flags
        comp.extend(tokens)

    packed_size = 14 + len(comp)
    return b"EnCode" + struct.pack("<II", len(decoded), packed_size) + bytes(comp)


def rebuild_pack(entries: Iterable[PackEntry], replacements: dict[int, bytes]) -> bytes:
    entries = list(entries)
    count = len(entries)
    table_end = 4 + count * 17
    cursor = table_end
    table = bytearray(struct.pack("<I", count))
    payloads: list[bytes] = []

    for entry in entries:
        blob = replacements.get(entry.index, entry.blob)
        table.extend(struct.pack("<I", cursor))
        table.extend(entry.raw_name)
        payloads.append(blob)
        cursor += len(blob)

    return bytes(table) + b"".join(payloads)


def patch_pack_in_place(entries: Iterable[PackEntry], replacements: dict[int, bytes]) -> bytes:
    entries = list(entries)
    payloads: list[bytes] = []
    table = bytearray(struct.pack("<I", len(entries)))
    for entry in entries:
        blob = replacements.get(entry.index, entry.blob)
        if len(blob) != len(entry.blob):
            raise ValueError(
                f"in-place replacement size mismatch for {entry.name}: "
                f"{len(entry.blob)} -> {len(blob)}"
            )
        table.extend(struct.pack("<I", entry.offset))
        table.extend(entry.raw_name)
        payloads.append(blob)
    return bytes(table) + b"".join(payloads)


def pad_encoded_entry_to_size(blob: bytes, target_size: int) -> bytes:
    if len(blob) > target_size:
        raise ValueError(f"encoded entry grew beyond original size: {len(blob)} > {target_size}")
    if not blob.startswith(b"EnCode"):
        raise ValueError("only EnCode entries can be padded")
    padded = bytearray(blob)
    struct.pack_into("<I", padded, 10, target_size)
    padded.extend(b"\0" * (target_size - len(padded)))
    return bytes(padded)


def find_long_punctuation_runs(decoded_entries: dict[str, bytes]) -> list[dict[str, object]]:
    suspicious: list[dict[str, object]] = []
    punct = set(b"#$%&*@")
    for name, decoded in decoded_entries.items():
        start: int | None = None
        for offset, value in enumerate(decoded + b"\0"):
            if value in punct:
                if start is None:
                    start = offset
            elif start is not None:
                if offset - start >= 4:
                    run = decoded[start:offset]
                    # Repeated @ runs and short @&/@$ command clusters are normal in
                    # these scripts. The known soft-lock pattern is a mixed punctuation
                    # run that looks like manual workaround notes: #, %, $, @, *, &.
                    if len(set(run)) >= 3:
                        suspicious.append(
                            {
                                "entry": name,
                                "offset_hex": f"0x{start:X}",
                                "bytes": run.hex(" "),
                                "ascii": run.decode("latin1"),
                            }
                        )
                start = None
    return suspicious


def repair_stor1107(decoded: bytes) -> tuple[bytes, list[Repair]]:
    """Repair the confirmed post-Mia soft-lock site in decoded STOR1107.BIN."""
    repaired = bytearray(decoded)
    repairs: list[Repair] = []

    anchor = "말좀 물읍".encode("cp949")
    anchor_offset = decoded.find(anchor)
    if anchor_offset < 4:
        raise ValueError("could not find the confirmed Mia dialogue anchor")
    source_header_offset = anchor_offset - 4
    source_header = decoded[source_header_offset:anchor_offset]

    corrupt_run = b"#%$#@*&%"
    replacement_run = b"@" * len(corrupt_run)
    corrupt_offset = decoded.find(corrupt_run)
    if corrupt_offset < 0:
        return bytes(repaired), repairs

    header_offset = corrupt_offset - 4
    if header_offset < 0:
        raise ValueError("corrupt run has no 4-byte header before it")

    header_before = decoded[header_offset:corrupt_offset]
    run_before = decoded[corrupt_offset : corrupt_offset + len(corrupt_run)]

    repaired[header_offset:corrupt_offset] = source_header
    repaired[corrupt_offset : corrupt_offset + len(corrupt_run)] = replacement_run
    repairs.append(
        Repair(
            entry_name="STOR1107.BIN",
            patch_mode="decoded-reencode",
            corrupt_offset=corrupt_offset,
            corrupt_run_before=run_before.hex(" "),
            corrupt_run_after=replacement_run.hex(" "),
            header_offset=header_offset,
            header_before=header_before.hex(" "),
            header_after=source_header.hex(" "),
            source_header_offset=source_header_offset,
            source_anchor_offset=anchor_offset,
        )
    )
    return bytes(repaired), repairs


def repair_stor1107_compressed(blob: bytes) -> tuple[bytes, list[Repair]]:
    """Patch only literal bytes in the original STOR1107 compressed stream.

    Re-encoding the whole entry is valid for our decoder, but the retail loader
    is old enough that preserving the original token stream is safer around
    scene transitions. This direct patch leaves every offset, flag byte, and ref
    token untouched; only the confirmed corrupt dialogue literal bytes change.
    """
    decoded_before = decode_entry(blob)
    corrupt_run = b"#%$#@*&%"
    replacement_run = b"@" * len(corrupt_run)
    corrupt_offset = decoded_before.find(corrupt_run)
    if corrupt_offset < 0:
        return blob, []

    literal_map = map_decoded_literals(blob)
    repaired = bytearray(blob)
    for index, replacement in enumerate(replacement_run):
        decoded_offset = corrupt_offset + index
        comp_pos = literal_map[decoded_offset]
        if comp_pos is None:
            raise ValueError(
                "confirmed corrupt run is not fully literal in the compressed stream: "
                f"decoded offset 0x{decoded_offset:X}"
            )
        repaired[comp_pos] = replacement

    repaired_blob = bytes(repaired)
    decoded_after = decode_entry(repaired_blob)
    expected_diffs = [
        corrupt_offset + index
        for index, (before, after) in enumerate(zip(corrupt_run, replacement_run))
        if before != after
    ]
    actual_diffs = [
        index
        for index, (before, after) in enumerate(zip(decoded_before, decoded_after))
        if before != after
    ]
    if actual_diffs != expected_diffs:
        raise ValueError(
            "compressed direct patch changed unexpected decoded bytes: "
            f"{[hex(offset) for offset in actual_diffs]}"
        )

    header_offset = corrupt_offset - 4
    if header_offset < 0:
        raise ValueError("corrupt run has no 4-byte header before it")

    anchor = "말좀 물읍".encode("cp949")
    anchor_offset = decoded_before.find(anchor)
    source_header_offset = anchor_offset - 4 if anchor_offset >= 4 else -1
    source_header = (
        decoded_before[source_header_offset:anchor_offset]
        if source_header_offset >= 0
        else b""
    )
    header_before = decoded_before[header_offset:corrupt_offset]
    repairs = [
        Repair(
            entry_name="STOR1107.BIN",
            patch_mode="compressed-literal-only",
            corrupt_offset=corrupt_offset,
            corrupt_run_before=corrupt_run.hex(" "),
            corrupt_run_after=replacement_run.hex(" "),
            header_offset=header_offset,
            header_before=header_before.hex(" "),
            header_after=header_before.hex(" "),
            source_header_offset=source_header_offset,
            source_anchor_offset=anchor_offset,
        )
    ]
    if source_header and header_before == source_header:
        repairs[0].header_after = source_header.hex(" ")
    return repaired_blob, repairs


def scan_and_patch_stage(stage_data: bytes) -> tuple[bytes, dict[str, object]]:
    entries = parse_pack(stage_data)
    decoded_entries = {entry.name: decode_entry(entry.blob) for entry in entries if entry.name}

    replacements: dict[int, bytes] = {}
    repairs: list[Repair] = []
    for entry in entries:
        if entry.name != "STOR1107.BIN":
            continue
        repaired_blob, entry_repairs = repair_stor1107_compressed(entry.blob)
        repairs.extend(entry_repairs)
        if repaired_blob != entry.blob:
            if len(repaired_blob) != len(entry.blob):
                raise ValueError("compressed direct patch changed STOR1107.BIN size")
            replacements[entry.index] = repaired_blob

    patched = patch_pack_in_place(entries, replacements) if replacements else stage_data
    patched_entries = parse_pack(patched)
    for entry in patched_entries:
        if entry.name and entry.blob.startswith(b"EnCode"):
            decode_entry(entry.blob)

    patched_decoded_entries = {
        entry.name: decode_entry(entry.blob) for entry in patched_entries if entry.name
    }
    suspicious_before = find_long_punctuation_runs(decoded_entries)
    suspicious_after = find_long_punctuation_runs(patched_decoded_entries)

    report = {
        "stage_sha256_before": sha256_hex(stage_data),
        "stage_sha256_after": sha256_hex(patched),
        "changed": patched != stage_data,
        "repairs": [dataclasses.asdict(repair) for repair in repairs],
        "suspicious_punctuation_runs_before": suspicious_before,
        "suspicious_punctuation_runs_after": suspicious_after,
        "encoded_entries_verified": sum(
            1 for entry in patched_entries if entry.name and entry.blob.startswith(b"EnCode")
        ),
        "entry_count": len(patched_entries),
        "stage_size_before": len(stage_data),
        "stage_size_after": len(patched),
    }
    return patched, report


def restore_backup(stage_path: Path, backup_path: Path) -> None:
    if not backup_path.exists():
        raise FileNotFoundError(f"backup does not exist: {backup_path}")
    shutil.copy2(backup_path, stage_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=Path("stage"))
    parser.add_argument("--apply", action="store_true", help="write the patched stage file")
    parser.add_argument("--restore", action="store_true", help="restore from the backup file")
    parser.add_argument(
        "--backup",
        type=Path,
        default=Path("stage.wftsp_dialogue_fix.bak"),
        help="backup path used by --apply and --restore",
    )
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    parser.add_argument("--force", action="store_true", help="allow patching an unknown stage hash")
    args = parser.parse_args(argv)

    if args.restore:
        restore_backup(args.stage, args.backup)
        print(f"restored {args.stage} from {args.backup}")
        return 0

    stage_data = args.stage.read_bytes()
    stage_hash = sha256_hex(stage_data)
    if (
        args.apply
        and stage_hash != EXPECTED_ORIGINAL_STAGE_SHA256
        and not args.force
        and not args.backup.exists()
    ):
        raise SystemExit(
            "stage hash is not the confirmed original and no backup exists; "
            "rerun with --force only after verifying the file manually"
        )

    patched, report = scan_and_patch_stage(stage_data)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.apply and patched != stage_data:
        if not args.backup.exists():
            shutil.copy2(args.stage, args.backup)
        args.stage.write_bytes(patched)
        print(f"patched {args.stage}; backup={args.backup}")
    elif args.apply:
        print("no changes needed")
    else:
        print("dry-run only; pass --apply to write the patched stage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
