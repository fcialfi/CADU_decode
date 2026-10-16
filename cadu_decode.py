#!/usr/bin/env python3
"""Decode the fixed fields of the transfer frames in a file of CADUs.

A CADU (CCSDS 131.0-B) is the 32-bit attached sync marker 1ACFFC1D followed
by a codeblock: the TM transfer frame (CCSDS 132.0-B) and its Reed-Solomon
(255,223) check symbols, the codeblock being randomized (CCSDS
pseudo-randomizer). With interleaving depth I = 5 a CADU is 4 + 255·5 =
1279 bytes and the transfer frame 223·5 = 1115 bytes.

For each CADU the script finds the sync marker, de-randomizes the codeblock
and prints the primary header and, when the frame has one, the Operational
Control Field (CLCW); the data field is skipped. It also reports the gaps in
the master and virtual channel frame counters.

    python cadu_decode.py capture.bin
    python cadu_decode.py capture.bin -o frames.csv          # every frame to a CSV file
    python cadu_decode.py capture.bin --interleave 4 --fecf  # other frame layout
    python cadu_decode.py capture.bin --not-randomized       # codeblocks sent in clear

The input is binary, or text with the bytes in hexadecimal (spaces and line
breaks ignored).
"""

import argparse
import csv
import string
import sys
from collections import Counter

ASM = bytes.fromhex("1ACFFC1D")
RS_N, RS_K = 255, 223


def pseudo_random_sequence(length: int) -> bytes:
    """CCSDS pseudo-random sequence, h(x) = x^8 + x^7 + x^5 + x^3 + 1, all ones at the start.

    Starts FF 48 0E C0 9A 0D 70 BC ...; it repeats every 255 bytes.
    """
    register, out = 0xFF, bytearray()
    for _ in range(length):
        byte = 0
        for _ in range(8):
            byte = (byte << 1) | (register >> 7)
            feedback = (register ^ (register >> 2) ^ (register >> 4) ^ (register >> 7)) & 1
            register = ((register << 1) | feedback) & 0xFF
        out.append(byte)
    return bytes(out)


def primary_header(frame: bytes) -> dict:
    """Fields of the 6-byte TM transfer frame primary header."""
    word = int.from_bytes(frame[0:2], "big")
    status = int.from_bytes(frame[4:6], "big")
    return {
        "version": word >> 14,
        "scid": (word >> 4) & 0x3FF,
        "vcid": (word >> 1) & 0x7,
        "ocf_flag": word & 0x1,
        "mc_count": frame[2],
        "vc_count": frame[3],
        "sec_header_flag": status >> 15,
        "sync_flag": (status >> 14) & 0x1,
        "packet_order_flag": (status >> 13) & 0x1,
        "segment_length_id": (status >> 11) & 0x3,
        "first_header_pointer": status & 0x7FF,
    }


def clcw(word: bytes) -> dict:
    """Fields of the Command Link Control Word (OCF type 0)."""
    value = int.from_bytes(word, "big")
    return {
        "clcw_type": value >> 31,
        "clcw_version": (value >> 29) & 0x3,
        "clcw_status": (value >> 26) & 0x7,
        "cop_in_effect": (value >> 24) & 0x3,
        "clcw_vcid": (value >> 18) & 0x3F,
        "no_rf_available": (value >> 15) & 0x1,
        "no_bit_lock": (value >> 14) & 0x1,
        "lockout": (value >> 13) & 0x1,
        "wait": (value >> 12) & 0x1,
        "retransmit": (value >> 11) & 0x1,
        "farm_b_counter": (value >> 9) & 0x3,
        "report_value": value & 0xFF,
    }


def first_header_pointer_text(fhp: int) -> str:
    if fhp == 0x7FF:
        return "no packet header"
    if fhp == 0x7FE:
        return "idle data only"
    return str(fhp)


def read_input(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    text = data.strip()
    if text and all(chr(c) in string.hexdigits + string.whitespace for c in text):
        return bytes.fromhex("".join(text.decode("ascii").split()))
    return data


def decode(data: bytes, interleave: int = 5, randomized: bool = True, fecf: bool = False):
    """Yield (offset, fields) for every CADU in ``data``, synchronised on the sync marker."""
    codeblock_len = RS_N * interleave
    frame_len = RS_K * interleave
    cadu_len = len(ASM) + codeblock_len
    prn = pseudo_random_sequence(codeblock_len) if randomized else None
    pos = data.find(ASM)
    while pos >= 0 and pos + cadu_len <= len(data):
        codeblock = data[pos + len(ASM):pos + cadu_len]
        if prn is not None:
            codeblock = bytes(a ^ b for a, b in zip(codeblock, prn))
        frame = codeblock[:frame_len]
        fields = primary_header(frame)
        trailer = frame_len - (2 if fecf else 0)
        if fields["ocf_flag"]:
            fields.update(clcw(frame[trailer - 4:trailer]))
        if fecf:
            fields["fecf"] = frame[-2:].hex().upper()
        yield pos, fields
        # The next CADU normally starts right after; otherwise search the next sync marker.
        nxt = pos + cadu_len
        pos = nxt if data[nxt:nxt + len(ASM)] == ASM else data.find(ASM, nxt)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="file of CADUs (binary, or hexadecimal text)")
    ap.add_argument("--interleave", type=int, default=5, help="RS interleaving depth I (default 5: 1279-byte CADU)")
    ap.add_argument("--not-randomized", action="store_true", help="the codeblocks are not randomized")
    ap.add_argument("--fecf", action="store_true", help="the frames end with a 2-byte FECF")
    ap.add_argument("-o", "--output", help="write every frame to this CSV file")
    ap.add_argument("--max-rows", type=int, default=50, help="frames printed on screen (default 50, 0: all)")
    args = ap.parse_args(argv)
    if not 1 <= args.interleave <= 8:
        ap.error("the interleaving depth must be 1 to 8")

    data = read_input(args.file)
    rows = [dict(offset=offset, **fields) for offset, fields in
            decode(data, args.interleave, not args.not_randomized, args.fecf)]
    cadu_len = len(ASM) + RS_N * args.interleave
    print(f"{args.file}: {len(data)} bytes, {len(rows)} CADUs of {cadu_len} bytes "
          f"(frame {RS_K * args.interleave} bytes, I = {args.interleave})")
    if not rows:
        print("No sync marker 1ACFFC1D found.")
        return 1

    head = ("#", "offset", "ver", "SCID", "VC", "OCF", "MCFC", "VCFC", "SH", "sync", "order", "seg", "FHP",
            "CLCW VC", "COP", "lockout", "wait", "retx", "FARM-B", "report")
    shown = rows if args.max_rows == 0 else rows[:args.max_rows]
    table = [head]
    for k, r in enumerate(shown, start=1):
        table.append((
            k, r["offset"], r["version"], r["scid"], r["vcid"], r["ocf_flag"], r["mc_count"], r["vc_count"],
            r["sec_header_flag"], r["sync_flag"], r["packet_order_flag"], r["segment_length_id"],
            first_header_pointer_text(r["first_header_pointer"]),
            *([r["clcw_vcid"], r["cop_in_effect"], r["lockout"], r["wait"], r["retransmit"], r["farm_b_counter"],
               r["report_value"]] if r["ocf_flag"] else ["–"] * 7),
        ))
    widths = [max(len(str(row[c])) for row in table) for c in range(len(head))]
    for row in table:
        print("  ".join(str(v).rjust(w) for v, w in zip(row, widths)))
    if len(shown) < len(rows):
        print(f"... {len(rows) - len(shown)} more frames (--max-rows 0 for all, -o for a CSV file)")

    # Summary: spacecraft, virtual channels, counter gaps, sync losses.
    print()
    print("Spacecraft IDs: " + ", ".join(f"{k} ({n})" for k, n in Counter(r["scid"] for r in rows).items()))
    print("Virtual channels: " + ", ".join(f"VC{k}: {n}" for k, n in sorted(Counter(r["vcid"] for r in rows).items())))
    jumps = sum(1 for a, b in zip(rows, rows[1:]) if b["offset"] - a["offset"] != cadu_len)
    mc_gaps = [(a, b) for a, b in zip(rows, rows[1:]) if (b["mc_count"] - a["mc_count"]) % 256 != 1]
    print(f"Master channel counter gaps: {len(mc_gaps)}" + (
        "  (first: " + ", ".join(f"{a['mc_count']}→{b['mc_count']}" for a, b in mc_gaps[:5]) + ")" if mc_gaps else ""))
    last_vc: dict = {}
    vc_gaps = Counter()
    for r in rows:
        prev = last_vc.get(r["vcid"])
        if prev is not None and (r["vc_count"] - prev) % 256 != 1:
            vc_gaps[r["vcid"]] += 1
        last_vc[r["vcid"]] = r["vc_count"]
    print("Virtual channel counter gaps: " + (", ".join(f"VC{k}: {n}" for k, n in sorted(vc_gaps.items()))
                                             if vc_gaps else "none"))
    if jumps:
        print(f"CADUs not contiguous (data between sync markers): {jumps}")
    if any(r["version"] != 0 for r in rows):
        print("Warning: transfer frame version not 0 in some frames: wrong layout, or try --not-randomized.")

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Frames written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
