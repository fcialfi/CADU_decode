#!/usr/bin/env python3
"""Decode the fixed fields of the transfer frames in a file of CADUs.

A CADU (CCSDS 131.0-B) is the 32-bit attached sync marker 1ACFFC1D followed
by a codeblock: the TM transfer frame (CCSDS 132.0-B) and its Reed-Solomon
(255,223) check symbols, the codeblock being randomized (CCSDS
pseudo-randomizer). With interleaving depth I = 5 a CADU is 4 + 255·5 =
1279 bytes and the transfer frame 223·5 = 1115 bytes.

For each CADU the script finds the sync marker, de-randomizes the codeblock
and reads the primary header and, when the frame has one, the Operational
Control Field (CLCW). It prints a summary of the
file: number of CADUs, spacecraft, virtual channels (frames, idle frames,
counter gaps), master channel counter and CLCW. The Reed-Solomon code is
decoded: frames corrected, frames uncorrectable and the frame error rate;
the fields of a corrected frame are read after the correction. The Space
Packets are rebuilt from the data fields of each virtual channel: packets
and bytes per VC and APID, idle packets, and the gaps of the packet
sequence counters.

    python cadu_decode.py capture.bin                        # summary
    python cadu_decode.py capture.bin --frames               # and the first 50 frames
    python cadu_decode.py capture.bin -o frames.csv          # every frame to a CSV file
    python cadu_decode.py capture.bin --show 125             # content of CADU number 125
    python cadu_decode.py capture.bin --exclude-bad          # without the uncorrectable frames
    python cadu_decode.py capture.bin --export-packets out    # packets of each APID to out/
    python cadu_decode.py capture.bin --interleave 4 --fecf  # other frame layout
    python cadu_decode.py capture.bin --randomization no     # codeblocks already de-randomized
                                                             # (default: detected)

The input is binary, or text with the bytes in hexadecimal (spaces and line
breaks ignored).

Author: Fabio Cialfi (Exostaff). Proprietary software, see LICENSE.
"""

import argparse
import csv
import os
import re
import string
import sys
from collections import Counter
from datetime import datetime

import rs_ccsds
from version import APP_AUTHOR, APP_NAME, APP_VERSION

ASM = bytes.fromhex("1ACFFC1D")
RS_N, RS_K = 255, 223
MAX_FRAME_LEN = 2048  # longest TM transfer frame (CCSDS 132.0-B)


def layout(interleave: int = 5, frame_length: int = 0) -> tuple[int, int, int]:
    """(sync marker bytes, block bytes, transfer frame bytes) of one unit of the file.

    ``frame_length`` 0: CADUs, sync marker + codeblock (frame and Reed-Solomon check symbols).
    Otherwise a file of bare transfer frames of that length, one after the other: no sync marker
    and no Reed-Solomon code; the block is the frame itself.
    """
    if frame_length:
        return 0, frame_length, frame_length
    return len(ASM), RS_N * interleave, RS_K * interleave


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


def _xor(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "big") ^ int.from_bytes(b[:len(a)], "big")).to_bytes(len(a), "big")


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


def detect_randomization(data: bytes, interleave: int = 5, fecf: bool = False, sample: int = 200,
                         frame_length: int = 0) -> bool:
    """Whether the codeblocks of ``data`` are randomized, judged on the first ``sample`` CADUs.

    Both readings are scored: frames with version 0, and master channel counters going up by 1 from a
    frame to the next. A file whose codeblocks were already de-randomized (as many receivers save
    them) reads with scrambled virtual channels and counters when de-randomized again, and the
    other way round.
    """
    def score(randomized):
        rows = []
        for _offset, fields in decode(data, interleave, randomized, fecf, frame_length):
            rows.append(fields)
            if len(rows) >= sample:
                break
        version_ok = sum(1 for r in rows if r["version"] == 0)
        consecutive = sum(1 for a, b in zip(rows, rows[1:]) if (b["mc_count"] - a["mc_count"]) % 256 == 1)
        return version_ok + consecutive

    return score(True) >= score(False)


def first_header_pointer_text(fhp: int) -> str:
    if fhp == 0x7FF:
        return "no packet header"
    if fhp == 0x7FE:
        return "idle data only"
    return str(fhp)


def concatenate(parts) -> tuple[bytes, list[tuple[int, str]]]:
    """Files read one after the other as one stream (chunks of one capture): ``parts`` is a list of
    (name, bytes). Returns the stream and the bounds, (start offset, name) of each file."""
    bounds, start = [], 0
    for name, data in parts:
        bounds.append((start, name))
        start += len(data)
    return b"".join(data for _, data in parts), bounds


def tag_files(rows, bounds) -> None:
    """Add to each row the file it starts in and its offset there ("file", "file_offset")."""
    k = 0
    for r in rows:
        while k + 1 < len(bounds) and bounds[k + 1][0] <= r["offset"]:
            k += 1
        r["file"], r["file_offset"] = bounds[k][1], r["offset"] - bounds[k][0]


def position_text(row: dict) -> str:
    """Where a frame is: "byte N of the file", or of one of the files concatenated."""
    if "file" in row:
        return f"byte {row['file_offset']} of {row['file']}"
    return f"byte {row['offset']} of the file"


_STAMP = re.compile(r"(\d{8}T\d{6})")


def pass_from_name(name: str) -> dict:
    """Satellite, station and pass times read from a file name, e.g.
    AWS1_H_1_20261028T150735_20261028T145625_20261028T150732_SVA.bin: satellite AWS1, file made at 15:07:35,
    pass from 14:56:25 to 15:07:32, station SVA. With three times the 2nd and 3rd are the pass (the 1st is
    when the file was made), with two they are the pass, one is the start. {} when the name has no time."""
    stem = os.path.splitext(os.path.basename(name.replace("\\", "/")))[0]
    times = []
    for text in _STAMP.findall(stem):
        try:
            times.append(datetime.strptime(text, "%Y%m%dT%H%M%S"))
        except ValueError:
            pass
    if not times:
        return {}
    start, end = (times[1], times[2]) if len(times) >= 3 else (times[0], times[1] if len(times) == 2 else None)
    if end is not None and end < start:
        end = None
    parts = stem.split("_")
    station = parts[-1] if len(parts) > 1 and parts[-1].isalpha() else ""
    satellite = parts[0] if len(parts) > 1 and not _STAMP.fullmatch(parts[0]) else ""
    return {"satellite": satellite, "station": station, "start": start, "end": end}


def read_input(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    text = data.strip()
    if text and all(chr(c) in string.hexdigits + string.whitespace for c in text):
        return bytes.fromhex("".join(text.decode("ascii").split()))
    return data


def decode(data: bytes, interleave: int = 5, randomized: bool = True, fecf: bool = False, frame_length: int = 0):
    """Yield (offset, fields) for every CADU in ``data``, synchronised on the sync marker.

    With ``frame_length`` the file holds bare transfer frames of that length, one after the other
    from the start of the file (see ``layout``)."""
    sync_len, codeblock_len, frame_len = layout(interleave, frame_length)
    cadu_len = sync_len + codeblock_len
    prn = pseudo_random_sequence(codeblock_len) if randomized else None
    pos = 0 if frame_length else data.find(ASM)
    while pos >= 0 and pos + cadu_len <= len(data):
        base = pos + sync_len

        def read(start, end):
            # Only the bytes read are de-randomized: fast on files of many CADUs.
            raw = data[base + start:base + end]
            return _xor(raw, prn[start:end]) if prn is not None else raw

        fields = primary_header(read(0, 6))
        trailer = frame_len - (2 if fecf else 0)
        if fields["ocf_flag"]:
            fields.update(clcw(read(trailer - 4, trailer)))
        if fecf:
            fields["fecf"] = read(frame_len - 2, frame_len).hex().upper()
        yield pos, fields
        # The next CADU normally starts right after; otherwise search the next sync marker.
        nxt = pos + cadu_len
        if frame_length:
            pos = nxt
        else:
            pos = nxt if data[nxt:nxt + len(ASM)] == ASM else data.find(ASM, nxt)


def detect_input(data: bytes, interleave: int = 5) -> str:
    """"cadu" when the file holds sync markers 1ACFFC1D one CADU apart, otherwise "frames"."""
    cadu_len = len(ASM) + RS_N * interleave
    pos = data.find(ASM, 0, 1 << 20)
    while pos >= 0:
        if data[pos + cadu_len:pos + cadu_len + len(ASM)] == ASM:
            return "cadu"
        pos = data.find(ASM, pos + 1, 1 << 20)
    # Another interleaving depth: still sync markers at a fixed distance
    marks = []
    pos = data.find(ASM, 0, 1 << 20)
    while pos >= 0 and len(marks) < 3:
        marks.append(pos)
        pos = data.find(ASM, pos + 1, 1 << 20)
    if len(marks) == 3 and marks[1] - marks[0] == marks[2] - marks[1]:
        return "cadu"
    return "frames"


def detect_frame_length(data: bytes, sample: int = 50) -> int:
    """Length of the transfer frames of a file of bare frames, or 0 when not found.

    Every length up to 2048 bytes is tried: the frames must keep version 0 and the spacecraft ID of the
    first one, with master channel counters going up by 1 (also read de-randomized, for randomized
    frames). The shortest length with the best score wins.
    """
    prn = pseudo_random_sequence(6)
    best, best_score = 0, 0
    for length in range(8, MAX_FRAME_LEN + 1):
        frames = min(sample, len(data) // length)
        if frames < 3:
            break
        for key in (None, prn):
            heads = [data[k * length:k * length + 6] for k in range(frames)]
            if key is not None:
                heads = [_xor(h, key) for h in heads]
            fields = [primary_header(h) for h in heads]
            if fields[0]["version"]:
                continue
            same = sum(1 for f in fields if f["version"] == 0 and f["scid"] == fields[0]["scid"])
            consecutive = sum(1 for a, b in zip(fields, fields[1:]) if (b["mc_count"] - a["mc_count"]) % 256 == 1)
            score = (same + consecutive) / (2 * frames - 1)
            if score > best_score + 1e-9:
                best, best_score = length, score
    return best if best_score >= 0.8 else 0


def parse_frame(frame: bytes, fecf: bool = False) -> dict:
    """Fields of a whole transfer frame: primary header, CLCW (with OCF), FECF."""
    fields = primary_header(frame[:6])
    trailer = len(frame) - (2 if fecf else 0)
    if fields["ocf_flag"]:
        fields.update(clcw(frame[trailer - 4:trailer]))
    if fecf:
        fields["fecf"] = frame[-2:].hex().upper()
    return fields


def check_reed_solomon(data: bytes, offsets, interleave: int = 5, randomized: bool = True,
                       progress=None) -> dict:
    """Reed-Solomon status of the CADUs at ``offsets``: offset -> (corrected codeblock, symbols per codeword).

    Only the CADUs with errors are listed (the others are error free). A count of -1 marks an
    uncorrectable codeword. With numpy the error-free codeblocks are found in one pass; without it
    every codeword is decoded (much slower).
    """
    codeblock_len = RS_N * interleave
    prn = pseudo_random_sequence(codeblock_len) if randomized else None

    def codeblock(offset):
        raw = data[offset + len(ASM):offset + len(ASM) + codeblock_len]
        return _xor(raw, prn) if prn is not None else raw

    result = {}
    offsets = list(offsets)
    try:
        import numpy as np
    except ImportError:
        np = None
    batch = 2000
    for start in range(0, len(offsets), batch):
        part = offsets[start:start + batch]
        if np is not None:
            blocks = np.frombuffer(b"".join(codeblock(o) for o in part), dtype=np.uint8).reshape(len(part), -1)
            suspects = [o for o, bad in zip(part, rs_ccsds.syndromes_nonzero(blocks, interleave)) if bad]
        else:
            suspects = part
        for offset in suspects:
            fixed, counts = rs_ccsds.codeblock_status(codeblock(offset), interleave)
            if any(counts):
                result[offset] = (fixed, counts)
        if progress is not None:
            progress(min(start + batch, len(offsets)), len(offsets))
    return result


def decode_all(data: bytes, interleave: int = 5, randomized: bool = True, fecf: bool = False,
               reed_solomon: bool = True, progress=None, frame_length: int = 0) -> list[dict]:
    """Every CADU as a dict: number n (from 1), offset, frame fields and, with ``reed_solomon``, rs
    ("ok", "corrected" or "uncorrectable"), rs_corrected (symbols corrected) and rs_failed (codewords
    not correctable). The fields of a corrected frame are read from the corrected bytes.

    ``progress(stage, done, total)`` is called now and then: stage "frames" (bytes read of the
    file), then "reed-solomon" (CADUs checked).

    Bare transfer frames (``frame_length``) have no Reed-Solomon code: ``reed_solomon`` is ignored.
    """
    rows = []
    for k, (o, f) in enumerate(decode(data, interleave, randomized, fecf, frame_length), start=1):
        rows.append(dict(n=k, offset=o, **f))
        if progress is not None and k % 2000 == 0:
            progress("frames", o, len(data))
    if progress is not None:
        progress("frames", len(data), len(data))
    if not reed_solomon or frame_length:
        return rows
    errors = check_reed_solomon(data, [r["offset"] for r in rows], interleave, randomized,
                                None if progress is None else lambda d, t: progress("reed-solomon", d, t))
    for r in rows:
        fixed, counts = errors.get(r["offset"], (None, [0]))
        failed = sum(1 for c in counts if c < 0)
        r["rs_corrected"] = sum(c for c in counts if c > 0)
        r["rs_failed"] = failed
        r["rs"] = "uncorrectable" if failed else ("corrected" if r["rs_corrected"] else "ok")
        if r["rs"] == "corrected":
            keep = {k: r[k] for k in ("n", "offset", "rs", "rs_corrected", "rs_failed")}
            r.clear()
            r.update(keep, **parse_frame(fixed[:RS_K * interleave], fecf))
    return rows


def _table(rows, left: int = 0, last_left: bool = False) -> list[str]:
    """Rows of values as aligned text lines (first row: headings).

    The first ``left`` columns, and the last one with ``last_left``, are left-aligned.
    """
    widths = [max(len(str(row[c])) for row in rows) for c in range(len(rows[0]))]
    last = len(widths) - 1
    return ["  ".join(str(v).ljust(w) if c < left or (last_left and c == last) else str(v).rjust(w)
                      for c, (v, w) in enumerate(zip(row, widths))).rstrip() for row in rows]


def _counter_gaps(counts) -> tuple[int, int]:
    """(number of jumps, frames missing) of a sequence of 8-bit frame counters."""
    jumps = missing = 0
    for a, b in zip(counts, counts[1:]):
        step = (b - a) % 256
        if step != 1:
            jumps += 1
            missing += (step - 1) % 256
    return jumps, missing


CLCW_KEYS = ("clcw_type", "clcw_version", "clcw_status", "cop_in_effect", "clcw_vcid", "no_rf_available",
             "no_bit_lock", "lockout", "wait", "retransmit", "farm_b_counter", "report_value")
CLCW_SHORT = {"clcw_type": "type", "clcw_version": "version", "clcw_status": "status", "cop_in_effect": "COP",
              "clcw_vcid": "VC", "no_rf_available": "NoRF", "no_bit_lock": "NoLock", "lockout": "lockout",
              "wait": "wait", "retransmit": "retx", "farm_b_counter": "FARM-B", "report_value": "report"}


def usual_clcw(rows):
    """The most frequent CLCW of the frames (field -> value), None without OCF."""
    words = Counter(tuple(r[k] for k in CLCW_KEYS) for r in rows if r["ocf_flag"])
    return dict(zip(CLCW_KEYS, words.most_common(1)[0][0])) if words else None


def clcw_differences(row: dict, usual: dict) -> dict:
    """The CLCW fields of ``row`` that differ from ``usual``: field -> (usual, value)."""
    if not row["ocf_flag"] or usual is None:
        return {}
    return {k: (usual[k], row[k]) for k in CLCW_KEYS if row[k] != usual[k]}


def errors_along_file(rows, segments: int = 10, edge: float = 0.05) -> list[str]:
    """Reed-Solomon errors in ``segments`` equal parts of the file, and the frame error rate without its edges.

    The CADUs are stored in order of arrival at a constant rate: the position in the file is the time in
    the pass. Errors grouped at the start and the end come from the acquisition and loss of the signal
    (low elevation, lock transients); errors spread over the pass are link errors.
    """
    n = len(rows)
    if n < segments or "rs" not in rows[0]:
        return []
    table = [("part", "CADUs", "frames", "corrected", "bytes corr.", "bytes/frame", "uncorrectable")]
    for k in range(segments):
        part = rows[k * n // segments:(k + 1) * n // segments]
        corrected = sum(1 for r in part if r["rs"] == "corrected")
        nbytes = sum(r["rs_corrected"] for r in part)
        table.append((f"{100 * k // segments}-{100 * (k + 1) // segments} %", f"{part[0]['n']}-{part[-1]['n']}",
                      len(part), corrected, nbytes, f"{nbytes / len(part):.2f}",
                      sum(1 for r in part if r["rs"] == "uncorrectable")))
    lines = ["  Errors along the file (position in the file = time in the pass):"]
    lines += ["    " + line for line in _table(table, left=1)]
    cut = max(1, int(n * edge))
    core = rows[cut:n - cut]
    edges_bad = sum(1 for r in rows[:cut] + rows[n - cut:] if r["rs"] == "uncorrectable")
    core_bad = sum(1 for r in core if r["rs"] == "uncorrectable")
    lines.append(f"  First and last {100 * edge:g} % ({cut} + {cut} CADUs): {edges_bad} uncorrectable; "
                 f"in between: {core_bad}/{len(core)} = {core_bad / len(core):.3e}" if core else "")
    return lines


def reed_solomon_lines(rows, max_listed: int = 50, list_bad: bool = True) -> list[str]:
    """Reed-Solomon results: frames without errors, corrected, uncorrectable, and the frame error rate.

    ``list_bad`` adds the numbers of the uncorrectable CADUs (not when they are excluded).
    """
    if not rows or "rs" not in rows[0]:
        return ["Reed-Solomon: not checked"]
    n = len(rows)
    status = Counter(r["rs"] for r in rows)
    corrected = sum(r["rs_corrected"] for r in rows)
    bad = [r for r in rows if r["rs"] == "uncorrectable"]
    lines = [
        f"Reed-Solomon (255,223): {status['ok']} frames without errors, {status['corrected']} corrected "
        f"({corrected} bytes), {len(bad)} uncorrectable",
        f"  Frame error rate (uncorrectable / all): {len(bad)}/{n} = {len(bad) / n:.3e} ({100 * len(bad) / n:.3f} %)",
        f"  Frames with errors before correction: {(status['corrected'] + len(bad)) / n:.3e}",
    ]
    lines += errors_along_file(rows)
    if bad and list_bad:
        numbers = ", ".join(str(r["n"]) for r in bad[:max_listed])
        lines.append(f"  Uncorrectable CADUs: {numbers}" + (f" ... ({len(bad) - max_listed} more)"
                                                              if len(bad) > max_listed else ""))
    return lines


def virtual_channel_table(good, unit_len: int = 0) -> list[tuple]:
    """Rows (headings first) of the virtual channel table of the frames ``good`` (without the uncorrectable ones).

    ``unit_len``: bytes of one CADU (or transfer frame) in the file; MB is the size of the VC in the file
    (frames x unit_len, 1 MB = 10^6 bytes)."""
    table = [("VC", "frames", "share", "MB", "idle", "packet start", "no header", "VCFC first", "VCFC last", "jumps",
              "missing", "RS corrected")]
    for vc in sorted({r["vcid"] for r in good}):
        frames = [r for r in good if r["vcid"] == vc]
        vc_jumps, vc_missing = _counter_gaps([r["vc_count"] for r in frames])
        fhp = Counter(r["first_header_pointer"] for r in frames)
        table.append((vc, len(frames), f"{100 * len(frames) / len(good):.1f} %",
                      f"{len(frames) * unit_len / 1e6:.2f}" if unit_len else "–", fhp[0x7FE],
                      len(frames) - fhp[0x7FE] - fhp[0x7FF], fhp[0x7FF], frames[0]["vc_count"],
                      frames[-1]["vc_count"], vc_jumps, vc_missing,
                      sum(1 for r in frames if r.get("rs") == "corrected") if "rs" in frames[0] else "–"))
    return table


IDLE_APID = 0x7FF  # idle packets (CCSDS 133.0-B)
SEQ_MOD = 1 << 14  # packet sequence count: 14 bits

# PUS-C (ECSS-E-ST-70-41C) TM packet data field header, after the 6-byte packet primary header:
# version (4 bits, = 2) and time reference status (4), service type (8), subtype (8), message type counter (16),
# destination ID (16), then the CUC time with its preamble (P-field): 15 bytes with P-field 2F (4 + 3 bytes).
# The housekeeping reports (service 3) carry their structure ID (SID) as the first field of the source data.
HK_REPORTS = {(3, 25): "HK report", (3, 26): "diagnostic report"}
SID_BYTES = 2  # 16-bit SID
PUS_HEAD = 48  # bytes read for the data field header and the SID (enough for any CUC time)


def pus_fields(packet: bytes) -> dict:
    """Service, subtype, time and SID of a TM packet with a PUS data field header ({} without one).

    Read from the first bytes of the packet (header included): "service" and "subtype" for PUS-A (version 1)
    and PUS-C (version 2); for PUS-C also "time" (CUC seconds, from the P-field) and, for the housekeeping and
    diagnostic reports (3,25 and 3,26), "sid" (16 bits, right after the time)."""
    if len(packet) < 9 or not (packet[0] >> 3) & 1 or (packet[0] >> 4) & 1:  # no secondary header, or a TC
        return {}
    version = packet[6] >> 4
    if version == 2:
        out = {"pus": 2, "service": packet[7], "subtype": packet[8]}
    elif (version & 0x7) == 1:  # PUS-A: spare bit, version 1 (3 bits), spare
        return {"pus": 1, "service": packet[7], "subtype": packet[8]}
    else:
        return {}
    pos = 13
    if len(packet) <= pos:
        return out
    pfield = packet[pos]
    coarse, fine = ((pfield >> 2) & 0x3) + 1, pfield & 0x3
    pos += 1
    if pfield & 0x80:  # second P-field octet: more coarse and fine octets
        if len(packet) <= pos:
            return out
        coarse += (packet[pos] >> 5) & 0x3
        fine += (packet[pos] >> 2) & 0x7
        pos += 1
    if (pfield >> 4) & 0x7 not in (1, 2) or len(packet) < pos + coarse + fine:  # not a CUC time code
        return out
    out["time"] = int.from_bytes(packet[pos:pos + coarse], "big") + (
        int.from_bytes(packet[pos + coarse:pos + coarse + fine], "big") / (1 << (8 * fine)) if fine else 0)
    pos += coarse + fine
    out["sid_offset"] = pos
    if (out["service"], out["subtype"]) in HK_REPORTS and len(packet) >= pos + SID_BYTES:
        out["sid"] = int.from_bytes(packet[pos:pos + SID_BYTES], "big")
    return out


EPOCHS = {  # epochs of the CUC time codes (P-field 2F: agency-defined epoch, see the mission ICD)
    "none": None,
    "gps": datetime(1980, 1, 6),
    "tai": datetime(1958, 1, 1),
    "unix": datetime(1970, 1, 1),
    "2000": datetime(2000, 1, 1),
}


def parse_epoch(text: str):
    """An epoch: none, gps, tai, unix, 2000, or a date as 2000-01-01 or 2000-01-01T12:00:00."""
    key = (text or "none").strip().lower().split()[0]
    if key in EPOCHS:
        return EPOCHS[key]
    return datetime.fromisoformat(text.strip())


def time_text(seconds, epoch=None) -> str:
    """A CUC time: the date with an epoch (no leap seconds: the time scale of the epoch), else the seconds."""
    if seconds is None:
        return "–"
    if epoch is None:
        return f"{seconds:.3f} s"
    from datetime import timedelta

    return (epoch + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def service_text(pus: dict) -> str:
    """"3,25" or "" without a PUS header."""
    return f"{pus['service']},{pus['subtype']}" if pus else ""


def frame_data_fields(data: bytes, rows, interleave: int = 5, randomized: bool = True, fecf: bool = False,
                      frame_length: int = 0):
    """Yield (row, data field) of every frame but the uncorrectable ones, corrected by the Reed-Solomon code."""
    sync_len, codeblock_len, frame_len = layout(interleave, frame_length)
    prn = pseudo_random_sequence(codeblock_len) if randomized else None
    for r in rows:
        if r.get("rs") == "uncorrectable":
            continue
        base = r["offset"] + sync_len
        if r.get("rs") == "corrected":
            block = data[base:base + codeblock_len]
            block = _xor(block, prn) if prn is not None else block
            frame = rs_ccsds.codeblock_status(block, interleave)[0][:frame_len]
        else:
            frame = data[base:base + frame_len]
            frame = _xor(frame, prn) if prn is not None else frame
        start = 6 + ((frame[6] & 0x3F) + 1 if r["sec_header_flag"] else 0)
        end = frame_len - (2 if fecf else 0) - (4 if r["ocf_flag"] else 0)
        yield r, frame[start:end]


def iter_packets(data: bytes, rows, interleave: int = 5, randomized: bool = True, fecf: bool = False,
                 keep_data: bool = False, vc_stats=None, progress=None, frame_length: int = 0):
    """Space Packets rebuilt from the frames, virtual channel by virtual channel, in order of arrival.

    Yields (vc, apid, seq_flags, seq_count, length, sec_header_flag, frame, chain, packet, pus), with ``frame``
    the number of the CADU where the packet starts, ``packet`` its bytes (with ``keep_data``, otherwise
    None), ``pus`` its PUS service, time and SID (see pus_fields) and ``chain`` a number that changes whenever the packet stream of the VC breaks: a frame of the
    VC lost (VC frame counter jump, e.g. an uncorrectable frame) or a packet that does not end where the
    next First Header Pointer says. The packets of one chain follow each other without anything lost.
    ``vc_stats`` (a dict) receives per VC: frames, idle_frames, packets, packet_bytes, idle_packet_bytes,
    cut (packets lost at a break), resyncs (breaks of the stream inside a VC with no frame lost),
    skipped_bytes (bytes before the first packet header after a break).
    """
    stats = {} if vc_stats is None else vc_stats
    state = {}
    chains = iter(range(1 << 62))
    total = len(rows)
    for k, (r, field) in enumerate(frame_data_fields(data, rows, interleave, randomized, fecf, frame_length)):
        if progress is not None and k % 2000 == 0:
            progress(r["n"], total)
        vc = r["vcid"]
        st = stats.setdefault(vc, Counter())
        s = state.get(vc)
        if s is None:
            s = state[vc] = {"buf": bytearray(), "synced": False, "count": None, "chain": next(chains),
                             "base": 0, "marks": []}
        st["frames"] += 1
        if s["count"] is not None and r["vc_count"] != (s["count"] + 1) % 256:
            if s["synced"] and s["buf"]:
                st["cut"] += 1
            s["buf"].clear()
            s["synced"] = False
        s["count"] = r["vc_count"]
        fhp = r["first_header_pointer"]
        if fhp == 0x7FE:  # idle data only
            st["idle_frames"] += 1
            continue
        buf = s["buf"]
        if not s["synced"]:
            if fhp == 0x7FF or fhp >= len(field):
                st["skipped_bytes"] += len(field)
                continue
            st["skipped_bytes"] += fhp
            buf[:] = field[fhp:]
            s["synced"], s["chain"] = True, next(chains)
            s["base"], s["marks"] = 0, [(0, r["n"])]
        else:
            if fhp == 0x7FF:
                s["marks"].append((s["base"] + len(buf), r["n"]))
                buf += field
            else:
                s["marks"].append((s["base"] + len(buf), r["n"]))
                buf += field[:fhp]
                yield from _extract(vc, s, st, keep_data)
                if buf or not s["synced"]:  # the packet does not end where the First Header Pointer says
                    st["resyncs"] += 1
                    s["chain"] = next(chains)
                buf[:] = field[fhp:]
                s["synced"], s["base"], s["marks"] = True, 0, [(0, r["n"])]
        yield from _extract(vc, s, st, keep_data)
        if not s["synced"]:  # a header that cannot be one: wait for the next First Header Pointer
            st["resyncs"] += 1
            s["chain"] = next(chains)


def _extract(vc, s, st, keep_data):
    """The complete packets at the head of the buffer of a virtual channel."""
    buf, marks = s["buf"], s["marks"]
    while s["synced"] and len(buf) >= 6:
        if buf[0] >> 5:  # packet version not 0: not a packet header, lost in the stream
            buf.clear()
            s["synced"] = False
            return
        length = int.from_bytes(buf[4:6], "big") + 7
        if len(buf) < length:
            return
        word = int.from_bytes(buf[0:2], "big")
        seq = int.from_bytes(buf[2:4], "big")
        while len(marks) > 1 and marks[1][0] <= s["base"]:
            marks.pop(0)
        apid = word & 0x7FF
        st["packets"] += 1
        st["packet_bytes"] += length
        if apid == IDLE_APID:
            st["idle_packet_bytes"] += length
        pus = pus_fields(bytes(buf[:min(length, PUS_HEAD)])) if (word >> 11) & 1 and apid != IDLE_APID else {}
        yield (vc, apid, seq >> 14, seq & 0x3FFF, length, (word >> 11) & 1, marks[0][1], s["chain"],
               bytes(buf[:length]) if keep_data else None, pus)
        del buf[:length]
        s["base"] += length


def packet_statistics(data: bytes, rows, interleave: int = 5, randomized: bool = True, fecf: bool = False,
                      max_gaps: int = 500, progress=None, frame_length: int = 0) -> dict:
    """Packets of the file per virtual channel and per APID, with the gaps of the sequence counters.

    A counter that goes back (by more than half its range: a jump of more than 8192 forward is read as one
    back) is counted as a back jump, not as packets missing: the packets of another stretch of time
    (e.g. a recorder dump after the real-time data, or dumped in blocks).
    Returns {"vcs": per VC counts (see iter_packets), "apids": one dict per (APID, VC), "gaps": the gaps
    of a sequence counter inside a chain (nothing lost in the frames: the packets missing never reached
    this VC; for each, the VCs where the missing counts were found), at most ``max_gaps`` per APID and VC}.
    """
    from array import array

    vcs = {}
    apids = {}
    gaps = []
    seqs = {}
    sids = {}
    services = {}
    all_times = []  # packet times (CUC) of the file
    for vc, apid, flags, seq, length, sec, frame, chain, _, pus in iter_packets(
            data, rows, interleave, randomized, fecf, vc_stats=vcs, progress=progress, frame_length=frame_length):
        if apid != IDLE_APID:
            key = (apid, pus.get("service"), pus.get("subtype"), vc)
            sv = services.get(key)
            if sv is None:
                sv = services[key] = {"apid": apid, "service": pus.get("service"), "subtype": pus.get("subtype"),
                                      "vc": vc, "packets": 0, "bytes": 0, "min_length": length,
                                      "max_length": length, "_times": []}
            sv["packets"] += 1
            sv["bytes"] += length
            sv["min_length"] = min(sv["min_length"], length)
            sv["max_length"] = max(sv["max_length"], length)
            if "time" in pus:
                sv["_times"].append(pus["time"])
                all_times.append(pus["time"])
        if "sid" in pus:
            key = (apid, pus["service"], pus["subtype"], pus["sid"], vc)
            h = sids.get(key)
            if h is None:
                h = sids[key] = {"apid": apid, "service": pus["service"], "subtype": pus["subtype"],
                                 "sid": pus["sid"], "vc": vc, "packets": 0, "bytes": 0, "min_length": length,
                                 "max_length": length, "first_frame": frame, "_times": []}
            h["packets"] += 1
            h["bytes"] += length
            h["min_length"] = min(h["min_length"], length)
            h["max_length"] = max(h["max_length"], length)
            h["_times"].append(pus["time"])
        a = apids.get((apid, vc))
        if a is None:
            a = apids[(apid, vc)] = {"apid": apid, "vc": vc, "packets": 0, "bytes": 0, "min_length": length,
                                     "max_length": length, "first_seq": seq, "last_seq": seq, "gaps": 0,
                                     "missing": 0, "gaps_lost_frames": 0, "missing_lost_frames": 0,
                                     "repeated": 0, "back_jumps": 0, "sec_header": sec, "services": Counter(),
                                     "_chain": chain, "_frame": frame}
            seqs[(apid, vc)] = array("H")
        else:
            step = (seq - a["last_seq"]) % SEQ_MOD
            if apid != IDLE_APID and step != 1:
                if step == 0:
                    a["repeated"] += 1
                elif step > SEQ_MOD // 2:  # the counter goes back: another stretch of data, not packets lost
                    a["back_jumps"] += 1
                elif chain == a["_chain"]:
                    a["gaps"] += 1
                    a["missing"] += step - 1
                    if a["gaps"] <= max_gaps:
                        gaps.append({"apid": apid, "vc": vc, "before": a["last_seq"], "after": seq,
                                     "missing": step - 1, "frame_before": a["_frame"], "frame": frame})
                else:
                    a["gaps_lost_frames"] += 1
                    a["missing_lost_frames"] += step - 1
        a["packets"] += 1
        a["bytes"] += length
        if pus:
            a["services"][(pus["service"], pus["subtype"])] += 1
        a["min_length"] = min(a["min_length"], length)
        a["max_length"] = max(a["max_length"], length)
        a["last_seq"], a["_chain"], a["_frame"] = seq, chain, frame
        seqs[(apid, vc)].append(seq)
    # Where the packets missing from a VC are: in another VC with the same APID?
    elsewhere = {}
    for g in gaps:
        if g["missing"] > 1000:
            g["found_in"] = []
            continue
        others = [vc for (apid, vc) in seqs if apid == g["apid"] and vc != g["vc"]]
        found = []
        for vc in others:
            key = (g["apid"], vc)
            if key not in elsewhere:
                elsewhere[key] = set(seqs[key])
            if any((g["before"] + i) % SEQ_MOD in elsewhere[key] for i in range(1, g["missing"] + 1)):
                found.append(vc)
        g["found_in"] = found
    table = sorted(apids.values(), key=lambda a: (a["apid"], a["vc"]))
    for a in table:
        del a["_chain"], a["_frame"]
    span, breaks = time_coverage(all_times)
    return {"vcs": vcs, "apids": table, "gaps": gaps, "sids": sid_table(sids.values()),
            "services": service_table(services.values(), span), "span": span, "time_breaks": breaks,
            "first_time": min(all_times) if all_times else None, "last_time": max(all_times) if all_times else None}


def time_coverage(times, min_break: float = 60.0, factor: float = 20.0):
    """(seconds covered by the packet times, breaks left out). The intervals between consecutive packet times
    are summed, except the breaks: longer than ``factor`` times the median interval and than ``min_break``
    seconds (a pause in the data, e.g. between the real-time data and a dump, or a packet with a wrong time).
    (None, 0) with fewer than two different times."""
    import statistics

    times = sorted(set(times))
    steps = [b - a for a, b in zip(times, times[1:])]
    if not steps:
        return None, 0
    limit = max(min_break, factor * statistics.median(steps))
    covered = sum(x for x in steps if x <= limit)
    return (covered or None), sum(1 for x in steps if x > limit)


def _median_step(times):
    """Median interval of the packet times (None with fewer than two different times)."""
    import statistics

    times = sorted(set(times))
    steps = [b - a for a, b in zip(times, times[1:])]
    return statistics.median(steps) if steps else None


def service_table(items, span) -> list[dict]:
    """The packets per APID, PUS service and VC (idle packets left out): bytes, share of all the bytes, period
    (median interval of the packet times) and rate. The rate is the bytes over the time the file covers
    (``span``: first to last packet time, in seconds), right for periodic and sporadic packets alike."""
    items = sorted(items, key=lambda v: (v["apid"], v["service"] is None, v["service"] or 0, v["subtype"] or 0,
                                         v["vc"]))
    total = sum(v["bytes"] for v in items)
    for v in items:
        v["period"] = _median_step(v.pop("_times"))
        v["share"] = v["bytes"] / total if total else 0
        v["bytes_per_s"] = v["bytes"] / span if span else None
        v["mb_per_day"] = v["bytes_per_s"] * 86400 / 1e6 if span else None
    return items


def service_totals(services) -> list[dict]:
    """The packets of each PUS service (all the APIDs together), largest first."""
    totals = {}
    for v in services:
        key = (v["service"], v["subtype"])
        t = totals.setdefault(key, {"service": v["service"], "subtype": v["subtype"], "apids": set(), "packets": 0,
                                    "bytes": 0, "share": 0, "bytes_per_s": None, "mb_per_day": None})
        t["apids"].add(v["apid"])
        t["packets"] += v["packets"]
        t["bytes"] += v["bytes"]
        t["share"] += v["share"]
        if v["bytes_per_s"] is not None:
            t["bytes_per_s"] = (t["bytes_per_s"] or 0) + v["bytes_per_s"]
            t["mb_per_day"] = (t["mb_per_day"] or 0) + v["mb_per_day"]
    return sorted(totals.values(), key=lambda t: -t["bytes"])


def _service_name(service, subtype) -> str:
    return "–" if service is None else f"{service},{subtype}"


def sid_table(items) -> list[dict]:
    """The housekeeping structures (one dict per APID, service, SID and VC) with their period: the median of the
    intervals between the packet times (CUC), the packets per day and the MB per day it gives."""
    import statistics

    table = []
    for h in sorted(items, key=lambda h: (h["apid"], h["service"], h["subtype"], h["sid"], h["vc"])):
        times = sorted(set(h.pop("_times")))
        steps = [b - a for a, b in zip(times, times[1:])]
        period = statistics.median(steps) if steps else None
        h["first_time"], h["last_time"] = (times[0], times[-1]) if times else (None, None)
        h["period"] = period
        h["per_day"] = 86400 / period if period else None
        h["mb_per_day"] = h["per_day"] * h["bytes"] / h["packets"] / 1e6 if period else None
        table.append(h)
    return table


DEFAULT_CHECKED_APIDS = "0, 100"  # time reporting and science data (Payload Main Application)


def parse_apids(text: str) -> set:
    """APIDs written as 100 or 15-16, separated by commas or spaces."""
    apids = set()
    for part in text.replace(",", " ").split():
        lo, _, hi = part.partition("-")
        lo, hi = int(lo), int(hi or lo)
        if not 0 <= lo <= hi <= 2047:
            raise ValueError(f"APIDs are 0 to 2047: {part}")
        apids.update(range(lo, hi + 1))
    return apids


def packet_lines(stats: dict, max_listed: int = 50, checked=None, epoch=None) -> list[str]:
    """Text of the packet statistics (see packet_statistics).

    The continuity of the sequence counts is given for the APIDs ``checked`` only (default: 0 and 100): the
    others may be filtered or sampled on board (only some of their reports recorded), so that their
    counters jump without anything being lost.
    """
    checked = parse_apids(DEFAULT_CHECKED_APIDS) if checked is None else set(checked)
    lines = ["Packets (rebuilt from the frames; uncorrectable frames left out):"]
    table = [("VC", "frames", "idle frames", "packets", "packet bytes", "MB", "MB without idle", "idle packets",
              "cut", "resyncs")]
    for vc in sorted(stats["vcs"]):
        v = stats["vcs"][vc]
        share = v["idle_packet_bytes"] / v["packet_bytes"] if v["packet_bytes"] else 0
        table.append((vc, v["frames"], v["idle_frames"], v["packets"], v["packet_bytes"],
                      f"{v['packet_bytes'] / 1e6:.2f}", f"{(v['packet_bytes'] - v['idle_packet_bytes']) / 1e6:.2f}",
                      f"{v['idle_packet_bytes']} B ({100 * share:.1f} %)", v["cut"], v["resyncs"]))
    lines += ["  " + line for line in _table(table)]
    lines.append("  idle packets: APID 2047; MB: 10^6 bytes; cut: packets lost where frames of the VC are missing; "
                 "resyncs: packet stream broken with no frame missing")
    lines.append("")
    table = [("APID", "VC", "packets", "bytes", "length", "services", "seq first", "seq last", "gaps", "missing",
              "at lost frames", "back jumps", "repeated")]
    for a in stats["apids"]:
        length = str(a["min_length"]) if a["min_length"] == a["max_length"] else \
            f"{a['min_length']}-{a['max_length']}"
        idle = a["apid"] == IDLE_APID
        check = a["apid"] in checked and not idle
        table.append((f"{a['apid']}" + (" idle" if idle else " *" if check else ""), a["vc"], a["packets"],
                      a["bytes"], length, services_text(a.get("services")), a["first_seq"], a["last_seq"],
                      a["gaps"] if check else "–",
                      a["missing"] if check else "–",
                      f"{a['gaps_lost_frames']} ({a['missing_lost_frames']} missing)" if check else "–",
                      a["back_jumps"] if check else "–", a["repeated"] if check else "–"))
    lines += ["  " + line for line in _table(table)]
    lines.append(f"  * continuity checked (APIDs {_apid_text(checked)}; --check-apids): gaps / missing: sequence "
                 "count jumps with no frame lost in between (packets that never reached this VC);")
    lines.append("    at lost frames: jumps where frames of the VC are missing; back jumps: the counter goes back "
                 "(another stretch of data), not counted as missing")
    gaps = [g for g in stats["gaps"] if g["apid"] in checked]
    if gaps:
        lines.append("")
        lines.append(f"  Sequence count gaps with no frame lost, APIDs checked ({len(gaps)}):")
        table = [("APID", "VC", "count before", "count after", "missing", "CADU before", "CADU after",
                  "missing counts found in")]
        for g in gaps[:max_listed]:
            table.append((g["apid"], g["vc"], g["before"], g["after"], g["missing"], g["frame_before"], g["frame"],
                          ", ".join(f"VC {v}" for v in g["found_in"]) or "–"))
        lines += ["    " + line for line in _table(table, last_left=True)]
        if len(gaps) > max_listed:
            lines.append(f"    ... {len(gaps) - max_listed} more")
    lines += service_lines(stats.get("services", []), stats.get("span"), stats, epoch)
    lines += sid_lines(stats.get("sids", []))
    return lines


def _rate_text(value, fmt) -> str:
    return "–" if value is None else format(value, fmt)


def coverage_text(stats: dict, epoch=None) -> str:
    """First and last packet time, and the time the packets cover (rates are computed over it)."""
    span = stats.get("span")
    if not span:
        return "no packet time: rates not computed"
    text = (f"packet times {time_text(stats.get('first_time'), epoch)} to {time_text(stats.get('last_time'), epoch)}; "
            f"rates over the {span:.0f} s ({span / 60:.1f} min) the packet times cover")
    if stats.get("time_breaks"):
        text += f" ({stats['time_breaks']} breaks in the times left out)"
    return text


def service_lines(services, span, stats=None, epoch=None) -> list[str]:
    """Text of the packets per PUS service: totals per service, then per APID and service."""
    lines = ["", "Packets by PUS service (idle packets left out; –: no PUS data field header, e.g. time reports):"]
    if not services:
        return lines + ["  none"]
    covered = coverage_text(stats if stats is not None else {"span": span}, epoch)
    table = [("service", "APIDs", "packets", "bytes", "share", "B/s", "MB/day")]
    for t in service_totals(services):
        table.append((_service_name(t["service"], t["subtype"]), _apid_text(t["apids"]), t["packets"], t["bytes"],
                      f"{100 * t['share']:.1f} %", _rate_text(t["bytes_per_s"], ".1f"),
                      _rate_text(t["mb_per_day"], ".3f")))
    total = sum(v["bytes"] for v in services)
    table.append(("total", "", sum(v["packets"] for v in services), total, "100.0 %",
                  _rate_text(total / span if span else None, ".1f"),
                  _rate_text(total / span * 86400 / 1e6 if span else None, ".3f")))
    lines += ["  " + line for line in _table(table, left=2)]
    lines.append(f"  {covered}; 1 MB = 10^6 bytes")
    lines += ["", "  By APID and service:"]
    table = [("APID", "service", "VC", "packets", "bytes", "share", "length", "period s", "B/s", "MB/day")]
    for v in services:
        length = str(v["min_length"]) if v["min_length"] == v["max_length"] else \
            f"{v['min_length']}-{v['max_length']}"
        table.append((v["apid"], _service_name(v["service"], v["subtype"]), v["vc"], v["packets"], v["bytes"],
                      f"{100 * v['share']:.1f} %", length, _period_text(v["period"]),
                      _rate_text(v["bytes_per_s"], ".1f"), _rate_text(v["mb_per_day"], ".3f")))
    lines += ["    " + line for line in _table(table)]
    lines.append("    period: median interval of the packet times (meaningful for periodic packets only)")
    return lines


def services_text(services) -> str:
    """PUS services of an APID, most frequent first: "3,25 2,9"; "–" with none."""
    if not services:
        return "–"
    return " ".join(f"{t},{s}" for (t, s), _ in services.most_common())


def _period_text(seconds) -> str:
    return "–" if seconds is None else f"{seconds:.3f}".rstrip("0").rstrip(".")


def sid_lines(sids) -> list[str]:
    """Text of the housekeeping structures found (SIDs of the PUS 3,25 and 3,26 reports)."""
    lines = ["", "Housekeeping reports by SID (PUS-C 3,25 HK and 3,26 diagnostic; SID: 16 bits after the "
                 "packet time):"]
    if not sids:
        return lines + ["  none (no PUS-C packet of service 3,25 or 3,26)"]
    table = [("APID", "service", "SID", "VC", "packets", "length", "period s", "packets/day", "MB/day")]
    for h in sids:
        length = str(h["min_length"]) if h["min_length"] == h["max_length"] else \
            f"{h['min_length']}-{h['max_length']}"
        table.append((h["apid"], f"{h['service']},{h['subtype']}", h["sid"], h["vc"], h["packets"], length,
                      _period_text(h["period"]), f"{h['per_day']:.0f}" if h["per_day"] else "–",
                      f"{h['mb_per_day']:.3f}" if h["mb_per_day"] else "–"))
    lines += ["  " + line for line in _table(table)]
    total = sum(h["mb_per_day"] for h in sids if h["mb_per_day"])
    lines.append(f"  {len({(h['apid'], h['sid']) for h in sids})} SIDs; period: median interval between the packet "
                 f"times of the SID; MB/day at that period: {total:.3f} in all (1 MB = 10^6 bytes)")
    return lines


def _apid_text(apids) -> str:
    """APIDs as 15-16, 100."""
    parts, run = [], []
    for a in sorted(apids):
        if run and a == run[-1] + 1:
            run.append(a)
            continue
        if run:
            parts.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
        run = [a]
    if run:
        parts.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return ", ".join(parts) or "none"


def export_packets(data: bytes, rows, folder: str, interleave: int = 5, randomized: bool = True,
                   fecf: bool = False, include_idle: bool = False, progress=None, frame_length: int = 0) -> dict:
    """Write the packets of each APID to <folder>/apid_<APID>.bin (in order of arrival) and the list of
    every packet to <folder>/packets.csv. Returns {APID: packets written}."""
    import os

    os.makedirs(folder, exist_ok=True)
    files, counts = {}, Counter()
    try:
        with open(os.path.join(folder, "packets.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(("vc", "apid", "seq_flags", "seq_count", "length", "sec_header_flag", "cadu", "chain",
                             "service", "subtype", "sid", "time"))
            for vc, apid, flags, seq, length, sec, frame, chain, packet, pus in iter_packets(
                    data, rows, interleave, randomized, fecf, keep_data=True, progress=progress,
                    frame_length=frame_length):
                if apid == IDLE_APID and not include_idle:
                    continue
                writer.writerow((vc, apid, flags, seq, length, sec, frame, chain, pus.get("service", ""),
                                 pus.get("subtype", ""), pus.get("sid", ""),
                                 f"{pus['time']:.6f}" if "time" in pus else ""))
                if apid not in files:
                    files[apid] = open(os.path.join(folder, f"apid_{apid:04d}.bin"), "wb")
                files[apid].write(packet)
                counts[apid] += 1
    finally:
        for f in files.values():
            f.close()
    return dict(counts)


def unaccounted_bytes(rows, data_len: int, unit_len: int) -> dict:
    """The bytes of the file in no CADU (or transfer frame): at the start, between them (offset, bytes) and at
    the end, and their total."""
    offsets = sorted(r["offset"] for r in rows)
    if not offsets:
        return {"lead": data_len, "gaps": [], "tail": 0, "total": data_len}
    gaps = [(a + unit_len, b - a - unit_len) for a, b in zip(offsets, offsets[1:]) if b - a > unit_len]
    lead, tail = offsets[0], max(0, data_len - offsets[-1] - unit_len)
    return {"lead": lead, "gaps": gaps, "tail": tail, "total": lead + sum(n for _, n in gaps) + tail}


def unaccounted_text(info: dict, unit: str = "CADUs") -> str:
    """One line on the bytes in no CADU or frame ("" when there are none)."""
    if not info["total"]:
        return ""
    parts = []
    if info["lead"]:
        parts.append(f"{info['lead']} at the start")
    if info["gaps"]:
        parts.append(f"{sum(n for _, n in info['gaps'])} in {len(info['gaps'])} stretches between them "
                     f"(first at byte {info['gaps'][0][0]})")
    if info["tail"]:
        parts.append(f"{info['tail']} at the end (not a whole frame)")
    return f"{info['total']} bytes of the file are in no {unit[:-1].lower()}: " + ", ".join(parts)


def single_vc_note(good) -> str:
    """With one virtual channel only in the file, the master channel counter jumps over the frames of the other
    VCs: not frames lost. "" otherwise."""
    vcs = {r["vcid"] for r in good}
    if len(vcs) != 1 or not _counter_gaps([r["mc_count"] for r in good])[1]:
        return ""
    return (f"only VC {vcs.pop()} is in the file: the master channel counter also counts the frames of the other "
            "VCs, not in this file; the frames lost are in the VC counter (Virtual channels: jumps, missing)")


def export_tables(stats: dict, folder: str, epoch=None) -> list[str]:
    """The packet tables of the summary as CSV files in ``folder``: vcs, apids, services, service_totals, sids,
    gaps. Returns the names of the files written."""
    os.makedirs(folder, exist_ok=True)

    def write(name, rows, fields):
        with open(os.path.join(folder, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
        return name

    vcs = [dict(vc=vc, **v) for vc, v in sorted(stats["vcs"].items())]
    sids = [dict(h, first=time_text(h["first_time"], epoch), last=time_text(h["last_time"], epoch))
            for h in stats.get("sids", [])]
    totals = [dict(t, apids=_apid_text(t["apids"])) for t in service_totals(stats.get("services", []))]
    return [
        write("vcs.csv", vcs, ["vc", "frames", "idle_frames", "packets", "packet_bytes", "idle_packet_bytes", "cut",
                               "resyncs"]),
        write("apids.csv", [dict(a, services=services_text(a.get("services"))) for a in stats["apids"]],
              ["apid", "vc", "packets", "bytes", "min_length", "max_length", "services", "first_seq", "last_seq",
               "gaps", "missing", "gaps_lost_frames", "missing_lost_frames", "back_jumps", "repeated"]),
        write("services.csv", stats.get("services", []),
              ["apid", "service", "subtype", "vc", "packets", "bytes", "share", "min_length", "max_length", "period",
               "bytes_per_s", "mb_per_day"]),
        write("service_totals.csv", totals,
              ["service", "subtype", "apids", "packets", "bytes", "share", "bytes_per_s", "mb_per_day"]),
        write("sids.csv", sids, ["apid", "service", "subtype", "sid", "vc", "packets", "bytes", "min_length",
                                 "max_length", "period", "per_day", "mb_per_day", "first", "last", "first_frame"]),
        write("gaps.csv", stats["gaps"], ["apid", "vc", "before", "after", "missing", "frame_before", "frame"]),
    ]


def summary(rows, data_len: int, cadu_len: int, max_listed: int = 50, all_rows=None, packets=None,
            checked_apids=None, unit: str = "CADUs", epoch=None) -> list[str]:
    """Summary of a file of CADUs: counts, Reed-Solomon, spacecraft, virtual channels, frame counters, CLCW
    and, with ``packets`` (from packet_statistics), the packets per VC and APID.

    ``rows`` are the frames analysed; ``all_rows`` every CADU of the file when some were excluded
    (uncorrectable frames).
    """
    all_rows = rows if all_rows is None else all_rows
    n = len(all_rows)
    lines = [f"{unit}: {n} of {cadu_len} bytes ({n * cadu_len} bytes)"]
    if n and "file" in all_rows[0]:
        per_file = Counter(r["file"] for r in all_rows)
        lines.append("Files concatenated (frames starting in each): "
                     + ", ".join(f"{name} {count}" for name, count in per_file.items()))
    lost = unaccounted_text(unaccounted_bytes(all_rows, data_len, cadu_len), unit) if n else ""
    if lost:
        lines.append(f"  {lost}")
    lines += (reed_solomon_lines(all_rows, max_listed, list_bad=len(rows) == n) if unit == "CADUs"
              else ["Reed-Solomon: none (transfer frames carry no check symbols)"])
    if len(rows) < n:
        lines.append(f"Analysis below on {len(rows)} frames: {n - len(rows)} uncorrectable frames excluded")
    if not rows:
        return lines
    good = [r for r in rows if r.get("rs") != "uncorrectable"]  # uncorrectable frames: header unreliable
    lines.append("Spacecraft IDs: " + ", ".join(f"{k} ({v} frames)" for k, v in Counter(r["scid"] for r in good).items())
                 + (" (uncorrectable frames not counted)" if len(good) < len(rows) else ""))
    versions = Counter(r["version"] for r in rows)
    if set(versions) != {0}:
        lines.append(f"Warning: transfer frame versions {dict(versions)}: wrong interleaving or randomization, "
                     "or frames with errors.")

    if good:
        jumps, missing = _counter_gaps([r["mc_count"] for r in good])
        lines.append(f"Master channel counter: {good[0]['mc_count']} → {good[-1]['mc_count']}, {jumps} jumps, "
                     f"about {missing} frames missing (8-bit counter: gaps modulo 256)")
        note = single_vc_note(good)
        if note:
            lines.append(f"  Note: {note}")

        lines.append("")
        lines.append("Virtual channels" + (" (uncorrectable frames not counted: their header is unreliable):"
                                           if len(good) < len(rows) else ":"))
        lines += ["  " + line for line in _table(virtual_channel_table(good, cadu_len))]
        lines.append(f"  MB: size in the file ({unit.lower()} of {cadu_len} bytes, 1 MB = 10^6 bytes); "
                     "idle: FHP 7FE (idle data only); packet start: a packet header in the frame; no header: FHP 7FF")

    with_ocf = [r for r in rows if r["ocf_flag"]]
    lines.append("")
    if not with_ocf:
        lines.append("CLCW: no frame with OCF")
    else:
        lines.append(f"CLCW: in {len(with_ocf)} frames")
        for key, label in (("clcw_vcid", "VC"), ("cop_in_effect", "COP in effect"), ("clcw_status", "status"),
                           ("report_value", "report value")):
            values = Counter(r[key] for r in with_ocf)
            lines.append(f"  {label}: " + ", ".join(f"{k} ({v})" for k, v in sorted(values.items())))
        flags = ", ".join(f"{label} {sum(r[key] for r in with_ocf)}" for key, label in (
            ("no_rf_available", "No RF available"), ("no_bit_lock", "No bit lock"), ("lockout", "Lockout"),
            ("wait", "Wait"), ("retransmit", "Retransmit")))
        lines.append(f"  frames with the flag set: {flags}")
        usual = usual_clcw(rows)
        odd = [(r.get("n", k), r, clcw_differences(r, usual)) for k, r in enumerate(rows, start=1)]
        odd = [item for item in odd if item[2]]
        lines.append("  most frequent CLCW: " + ", ".join(f"{CLCW_SHORT[k]} {usual[k]}" for k in CLCW_KEYS)
                     + f" ({len(with_ocf) - len(odd)} frames)")
        if not odd:
            lines.append("  every CLCW is the most frequent one")
        else:
            lines.append(f"  CADUs with a different CLCW: {len(odd)} (changes while commanding, or frames with "
                         "errors" + (": the Reed-Solomon code is not checked)" if "rs" not in rows[0] else
                                     " the Reed-Solomon code could not correct)"))
            table = [("CADU #", "offset", "VC", "MCFC", "different fields (most frequent → this frame)")]
            for k, r, diff in odd[:max_listed]:
                table.append((k, r["offset"], r["vcid"], r["mc_count"],
                              ", ".join(f"{CLCW_SHORT[f]} {a}→{b}" for f, (a, b) in diff.items())))
            lines += ["    " + line for line in _table(table, last_left=True)]
            if len(odd) > max_listed:
                lines.append(f"    ... {len(odd) - max_listed} more (--frames and -o list every frame)")
    if packets is not None:
        lines.append("")
        lines += packet_lines(packets, max_listed, checked_apids, epoch)
    return lines


def frame_lines(rows) -> list[str]:
    files = bool(rows) and "file" in rows[0]  # files concatenated: where each frame comes from
    head = ("#", "offset", *(("file", "file offset") if files else ()), "ver", "SCID", "VC", "OCF", "MCFC", "VCFC",
            "SH", "sync", "order", "seg", "FHP", "CLCW VC", "COP", "lockout", "wait", "retx", "FARM-B", "report", "RS")
    table = [head]
    for k, r in enumerate(rows, start=1):
        table.append((
            r.get("n", k), r["offset"], *((r["file"], r["file_offset"]) if files else ()), r["version"], r["scid"],
            r["vcid"], r["ocf_flag"], r["mc_count"], r["vc_count"],
            r["sec_header_flag"], r["sync_flag"], r["packet_order_flag"], r["segment_length_id"],
            first_header_pointer_text(r["first_header_pointer"]),
            *([r["clcw_vcid"], r["cop_in_effect"], r["lockout"], r["wait"], r["retransmit"], r["farm_b_counter"],
               r["report_value"]] if r["ocf_flag"] else ["–"] * 7),
            rs_text(r),
        ))
    return _table(table)


def rs_text(row: dict) -> str:
    if "rs" not in row:
        return "–"
    if row["rs"] == "corrected":
        return f"corr. {row['rs_corrected']}"
    return "FAIL" if row["rs"] == "uncorrectable" else "ok"


PRIMARY_HEADER_FIELDS = (  # (key, name, bits)
    ("version", "Transfer Frame Version", 2), ("scid", "Spacecraft ID", 10), ("vcid", "Virtual Channel ID", 3),
    ("ocf_flag", "OCF Flag", 1), ("mc_count", "Master Channel Frame Count", 8),
    ("vc_count", "Virtual Channel Frame Count", 8), ("sec_header_flag", "Secondary Header Flag", 1),
    ("sync_flag", "Synch Flag", 1), ("packet_order_flag", "Packet Order Flag", 1),
    ("segment_length_id", "Segment Length ID", 2), ("first_header_pointer", "First Header Pointer", 11),
)
CLCW_FIELDS = (
    ("clcw_type", "Control Word Type", 1), ("clcw_version", "CLCW Version", 2), ("clcw_status", "Status Field", 3),
    ("cop_in_effect", "COP in Effect", 2), ("clcw_vcid", "Virtual Channel ID", 6), (None, "Spare", 2),
    ("no_rf_available", "No RF Available", 1), ("no_bit_lock", "No Bit Lock", 1), ("lockout", "Lockout", 1),
    ("wait", "Wait", 1), ("retransmit", "Retransmit", 1), ("farm_b_counter", "FARM-B Counter", 2),
    (None, "Spare", 1), ("report_value", "Report Value", 8),
)


def parse_selection(text: str, count: int) -> list[int]:
    """CADU numbers (1-based) of a selection like "5", "5,8" or "10-12"; "last" is the last one."""
    numbers = []
    for part in text.replace(" ", "").split(","):
        part = part.replace("last", str(count))
        first, _, last = part.partition("-")
        lo, hi = int(first), int(last or first)
        if not 1 <= lo <= hi <= count:
            raise ValueError(f"CADU {part} is not between 1 and {count}")
        numbers += range(lo, hi + 1)
    return numbers


def codeblock_at(data: bytes, offset: int, interleave: int = 5, randomized: bool = True,
                 frame_length: int = 0) -> tuple[bytes, bytes]:
    """(CADU as received, de-randomized codeblock) of the CADU starting at ``offset``.

    Bare transfer frames (``frame_length``): (frame as received, de-randomized frame)."""
    sync_len, block_len, _ = layout(interleave, frame_length)
    cadu = data[offset:offset + sync_len + block_len]
    codeblock = cadu[sync_len:]
    if randomized:
        codeblock = _xor(codeblock, pseudo_random_sequence(len(codeblock)))
    return cadu, codeblock


def packet_headers(data_field: bytes, fhp: int) -> list[dict]:
    """Space Packet primary headers that start in a frame data field, from the First Header Pointer."""
    if fhp in (0x7FE, 0x7FF):
        return []
    packets, pos = [], fhp
    while pos < len(data_field):
        if pos + 6 > len(data_field):
            packets.append({"offset": pos, "continues": True})
            break
        word = int.from_bytes(data_field[pos:pos + 2], "big")
        seq = int.from_bytes(data_field[pos + 2:pos + 4], "big")
        length = int.from_bytes(data_field[pos + 4:pos + 6], "big") + 7  # total, header included
        packets.append({
            "offset": pos, "version": word >> 13, "type": (word >> 12) & 1, "sec_header_flag": (word >> 11) & 1,
            "apid": word & 0x7FF, "sequence_flags": seq >> 14, "sequence_count": seq & 0x3FFF, "length": length,
            "continues": pos + length > len(data_field),
            "pus": pus_fields(data_field[pos:pos + min(length, PUS_HEAD)]) if word & 0x7FF != IDLE_APID else {},
        })
        pos += length
    return packets


def _bit_table(word: int, total_bits: int, fields, values: dict) -> list[str]:
    rows = [("field", "bits", "binary", "dec", "hex")]
    shift = total_bits
    for key, name, bits in fields:
        shift -= bits
        raw = (word >> shift) & ((1 << bits) - 1)
        rows.append((name, bits, format(raw, f"0{bits}b"), values.get(key, raw) if key else raw,
                     f"{raw:0{(bits + 3) // 4}X}"))
    return _table(rows, left=1)


def hex_dump(block: bytes, marks: list[tuple[int, str]]) -> list[str]:
    """16 bytes per line with the offset, the section names given as (offset, name) on their own line."""
    lines, marks = [], sorted(marks)
    for start in range(0, len(block), 16):
        for offset, name in marks:
            if start <= offset < start + 16:
                lines.append(f"        -- {name} at byte {offset}")
        chunk = block[start:start + 16]
        lines.append(f"  {start:5d}  " + " ".join(f"{b:02X}" for b in chunk))
    return lines


def show_cadu(number: int, row: dict, data: bytes, interleave: int, randomized: bool, fecf: bool,
              dump: bool = True, reed_solomon: bool = True, frame_length: int = 0) -> list[str]:
    """Detailed content of one CADU: sync marker, Reed-Solomon, header and CLCW fields, packets, hex dump.

    Bare transfer frames (``frame_length``): the frame, with no sync marker and no Reed-Solomon code."""
    cadu, codeblock = codeblock_at(data, row["offset"], interleave, randomized, frame_length)
    _, _, frame_len = layout(interleave, frame_length)
    rs_lines = []
    if reed_solomon and not frame_length:
        fixed, counts = rs_ccsds.codeblock_status(codeblock, interleave)
        words = ", ".join(f"{i + 1}: " + ("uncorrectable" if c < 0 else f"{c} corrected" if c else "ok")
                          for i, c in enumerate(counts))
        rs_lines.append(f"Reed-Solomon, codewords ({interleave} interleaved): {words}")
        changed = [i for i in range(len(codeblock)) if fixed[i] != codeblock[i]]
        if changed:
            rs_lines.append(f"  bytes corrected at offsets {', '.join(map(str, changed))}; shown corrected below")
            codeblock = fixed
        if any(c < 0 for c in counts):
            rs_lines.append("  uncorrectable: the fields below may be wrong")
        row = dict(row, **parse_frame(codeblock[:frame_len], fecf))
    frame = codeblock[:frame_len]
    end = frame_len - (2 if fecf else 0) - (4 if row["ocf_flag"] else 0)
    start = 6
    if row["sec_header_flag"]:
        start += (frame[6] & 0x3F) + 1
    if frame_length:
        lines = [f"Transfer frame #{number} at {position_text(row)} ({len(cadu)} bytes)",
                 f"Frame: {'de-randomized' if randomized else 'not randomized'}; no sync marker, "
                 "no Reed-Solomon code (file of transfer frames)"]
    else:
        lines = [f"CADU #{number} at {position_text(row)} ({len(cadu)} bytes)",
                 f"Sync marker: {cadu[:4].hex().upper()}" + ("" if cadu[:4] == ASM else "  (not 1ACFFC1D)"),
                 f"Codeblock: {'de-randomized' if randomized else 'not randomized'}; transfer frame {frame_len} "
                 f"bytes, RS check symbols {len(codeblock) - frame_len} bytes"]
    lines += [*rs_lines,
             "",
             f"Primary header (bytes 0-5): {frame[:6].hex(' ').upper()}"]
    lines += ["  " + line for line in _bit_table(int.from_bytes(frame[:6], "big"), 48, PRIMARY_HEADER_FIELDS, {})]
    lines.append(f"  First Header Pointer: {first_header_pointer_text(row['first_header_pointer'])}")
    if row["sec_header_flag"]:
        lines.append(f"Secondary header (bytes 6-{start - 1}): {frame[6:start].hex(' ').upper()}")
    data_field = frame[start:end]
    lines += ["", f"Data field: bytes {start}-{end - 1} ({len(data_field)} bytes)"]
    packets = packet_headers(data_field, row["first_header_pointer"])
    if row["first_header_pointer"] == 0x7FE:
        lines.append("  idle data only")
    elif row["first_header_pointer"] == 0x7FF:
        lines.append("  no packet starts in this frame (continuation of a packet)")
    elif row["first_header_pointer"] >= len(data_field):
        lines.append(f"  First Header Pointer {row['first_header_pointer']} beyond the data field: not valid "
                     "(frame with errors?)")
    else:
        if row["first_header_pointer"]:
            lines.append(f"  bytes 0-{row['first_header_pointer'] - 1}: end of a packet started in a previous frame")
        table = [("at byte", "ver", "type", "SH", "APID", "seq flags", "seq count", "length", "service", "SID", "")]
        for p in packets:
            if "apid" not in p:
                table.append((p["offset"], "", "", "", "", "", "", "", "", "", "header continues in the next frame"))
            else:
                table.append((p["offset"], p["version"], p["type"], p["sec_header_flag"], p["apid"],
                              f"{p['sequence_flags']:02b}", p["sequence_count"], p["length"],
                              service_text(p["pus"]) or "–", p["pus"].get("sid", "–"),
                              "continues in the next frame" if p["continues"] else ""))
        lines += ["  Packets starting in this frame (offsets in the data field, length with header):"]
        lines += ["  " + line for line in _table(table)]
    if row["ocf_flag"]:
        ocf = frame[end:end + 4]
        lines += ["", f"OCF / CLCW (bytes {end}-{end + 3}): {ocf.hex(' ').upper()}"]
        lines += ["  " + line for line in _bit_table(int.from_bytes(ocf, "big"), 32, CLCW_FIELDS, {})]
    if fecf:
        lines.append(f"FECF (bytes {frame_len - 2}-{frame_len - 1}): {frame[-2:].hex(' ').upper()}")
    if dump:
        marks = [(0, "primary header"), (start, "data field")]
        if row["sec_header_flag"]:
            marks.append((6, "secondary header"))
        if row["ocf_flag"]:
            marks.append((end, "OCF"))
        if fecf:
            marks.append((frame_len - 2, "FECF"))
        if not frame_length:
            marks.append((frame_len, "Reed-Solomon check symbols"))
        what = "Transfer frame" if frame_length else "Codeblock"
        lines += ["", f"{what} (de-randomized), offsets from the start of the transfer frame:"
                  if randomized else f"{what}, offsets from the start of the transfer frame:"]
        lines += hex_dump(codeblock, marks)
    return lines


def prepare(data: bytes, interleave: int = 5, input_kind: str = "auto", frame_length: int = 0,
            randomization: str = "auto", fecf: bool = False) -> dict:
    """How to read ``data``: frame_length (0: CADUs) and how it was found, randomized and how it was found.

    ``input_kind`` auto / cadu / frames; ``frame_length`` > 0 sets the length of the transfer frames;
    ``randomization`` auto / yes / no."""
    kind = "frames" if frame_length else input_kind
    if kind == "auto":
        kind = detect_input(data, interleave)
    length, length_how = 0, ""
    if kind == "frames":
        length, length_how = frame_length, "option"
        if not length:
            length, length_how = detect_frame_length(data), "detected"
        if not length:
            length, length_how = RS_K * interleave, f"not detected: 223 x I = {RS_K * interleave}"
    if randomization == "no":
        randomized, how = False, "not randomized (option)"
    elif randomization == "yes":
        randomized, how = True, "randomized (option)"
    else:
        randomized = detect_randomization(data, interleave, fecf, frame_length=length)
        how = ("randomized" if randomized else "not randomized, already de-randomized") + " (detected)"
    return {"frame_length": length, "length_how": length_how, "randomized": randomized, "randomized_how": how}


def file_overview(name: str, data: bytes, interleave: int = 5, input_kind: str = "auto", frame_length: int = 0,
                  randomization: str = "auto", fecf: bool = False, reed_solomon: bool = True,
                  packets: bool = True, report: bool = False, checked_apids=None, exclude_bad: bool = False,
                  max_listed: int = 50) -> dict:
    """The main numbers of one file, for the overview of several files (one row each).

    With ``report`` also "report": the lines of its full summary (as for the file alone)."""
    how = prepare(data, interleave, input_kind, frame_length, randomization, fecf)
    length = how["frame_length"]
    rows = decode_all(data, interleave, how["randomized"], fecf, reed_solomon=reed_solomon, frame_length=length)
    sync_len, block_len, frame_len = layout(interleave, length)
    good = [r for r in rows if r.get("rs") != "uncorrectable"]
    stats = packet_statistics(data, rows, interleave, how["randomized"], fecf, frame_length=length) if packets else None
    vcs = Counter(r["vcid"] for r in good)
    out = {
        "file": os.path.basename(name), **pass_from_name(name), "bytes": len(data),
        "input": f"TF {frame_len} B" if length else f"CADU {sync_len + block_len} B",
        "frames": len(rows), "scids": sorted({r["scid"] for r in good}),
        "vcs": dict(sorted(vcs.items())),
        "vc_mb": {vc: n * (sync_len + block_len) / 1e6 for vc, n in sorted(vcs.items())},
        "idle_frames": sum(1 for r in good if r["first_header_pointer"] == 0x7FE),
        "mc_missing": _counter_gaps([r["mc_count"] for r in good])[1] if good else 0,
        "rs": "rs" in rows[0] if rows else False,
        "corrected": sum(1 for r in rows if r.get("rs") == "corrected"),
        "uncorrectable": len(rows) - len(good),
    }
    if stats is not None:
        vc_stats = stats["vcs"].values()
        out["packets"] = sum(v["packets"] for v in vc_stats)
        out["packet_mb"] = sum(v["packet_bytes"] - v["idle_packet_bytes"] for v in vc_stats) / 1e6
    if report:
        if length:
            line = (f"{len(data)} bytes; transfer frames of {frame_len} bytes ({how['length_how']}), no sync marker, "
                    f"no Reed-Solomon code; frames {how['randomized_how']}")
        else:
            line = (f"{len(data)} bytes; CADU {sync_len + block_len} bytes, transfer frame {frame_len} bytes "
                    f"(I = {interleave}); codeblocks {how['randomized_how']}")
        analysed = good if exclude_bad else rows
        out["report"] = [line] + (summary(analysed, len(data), sync_len + block_len, max_listed, all_rows=rows,
                                          packets=stats, checked_apids=checked_apids,
                                          unit="Transfer frames" if length else "CADUs")
                                  if rows else ["The file is shorter than one transfer frame." if length
                                                else "No sync marker 1ACFFC1D found."])
    return out


def overview_reports(items) -> list[str]:
    """The full summary of each file (file_overview with ``report``), in the order of the overview table."""
    lines = []
    for o in _by_pass(items):
        title = o["file"]
        if o.get("start"):
            title += (f"  —  {o.get('satellite') or ''} pass {_when(o['start'])}"
                      + (f" → {o['end']:%H:%M:%S}" if o.get("end") else "")
                      + (f", station {o['station']}" if o.get("station") else ""))
        lines += ["", "=" * 100, title, "=" * 100] + o.get("report", [])
    return lines


def _by_pass(items):
    return sorted(items, key=lambda o: (o.get("start") is None, o.get("start") or datetime.min, o["file"]))


def _when(t) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else ""


def overview_table(items) -> list[tuple]:
    """Rows (headings first, totals last) of the overview of several files, in order of pass start."""
    items = _by_pass(items)
    table = [("sat", "station", "pass start", "end", "min", "input", "frames", "VC: frames", "MB",
              "idle frames", "MC missing", "RS corrected", "RS uncorrectable", "FER", "packets", "packet MB", "file")]
    for o in items:
        minutes = f"{(o['end'] - o['start']).total_seconds() / 60:.1f}" if o.get("end") else ""
        fer = f"{o['uncorrectable'] / o['frames']:.2e}" if o["rs"] and o["frames"] else "–"
        table.append((o.get("satellite", ""), o.get("station", ""), _when(o.get("start")),
                      o["end"].strftime("%H:%M:%S") if o.get("end") else "", minutes, o["input"], o["frames"],
                      " ".join(f"{vc}:{n}" for vc, n in o["vcs"].items()) or "–", f"{o['bytes'] / 1e6:.2f}",
                      o["idle_frames"], o["mc_missing"], o["corrected"] if o["rs"] else "–",
                      o["uncorrectable"] if o["rs"] else "–", fer, o.get("packets", "–"),
                      f"{o['packet_mb']:.2f}" if "packet_mb" in o else "–", o["file"]))
    if len(items) > 1:
        frames = sum(o["frames"] for o in items)
        checked = [o for o in items if o["rs"]]  # Reed-Solomon totals: the files of CADUs checked only
        bad = sum(o["uncorrectable"] for o in checked)
        rs_frames = sum(o["frames"] for o in checked)
        rs = bool(checked)
        vcs = Counter()
        for o in items:
            vcs.update(o["vcs"])
        table.append(("total", "", "", "", "", "", frames,
                      " ".join(f"{vc}:{n}" for vc, n in sorted(vcs.items())),
                      f"{sum(o['bytes'] for o in items) / 1e6:.2f}", sum(o["idle_frames"] for o in items),
                      sum(o["mc_missing"] for o in items), sum(o["corrected"] for o in checked) if rs else "–",
                      bad if rs else "–", f"{bad / rs_frames:.2e}" if rs and rs_frames else "–",
                      sum(o.get("packets", 0) for o in items) if all("packets" in o for o in items) else "–",
                      f"{sum(o['packet_mb'] for o in items):.2f}" if all("packet_mb" in o for o in items) else "–",
                      f"{len(items)} files"))
    return table


def overview_lines(items) -> list[str]:
    lines = ["Overview by file (pass times from the file names; MB: size of the file; packet MB: packets "
             "without the idle ones; 1 MB = 10^6 bytes):"]
    lines += ["  " + line for line in _table(overview_table(items), left=3, last_left=True)]
    if any(o["rs"] for o in items) and not all(o["rs"] for o in items):
        lines.append("  Reed-Solomon totals: the files of CADUs only (transfer frames have no Reed-Solomon code)")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="+", help="file of CADUs or of transfer frames (binary, or hexadecimal text); "
                                            "several files: one summary each, or one stream with --concat")
    ap.add_argument("--concat", action="store_true",
                    help="read the files one after the other as one stream (chunks of one capture, in the order "
                         "given): one summary; every frame says which file it comes from")
    ap.add_argument("--overview", action="store_true",
                    help="several files: first a table with a row per file (pass date and station from the file "
                         "name, frames, VCs, MB, Reed-Solomon, packets) and the totals, then the summary of each "
                         "file in the same order")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION} - author: {APP_AUTHOR}")
    ap.add_argument("--input", choices=("auto", "cadu", "frames"), default="auto",
                    help="what the file holds: cadu (sync marker 1ACFFC1D + codeblock), frames (bare transfer "
                         "frames one after the other: no sync marker, no Reed-Solomon code) or auto (default: "
                         "cadu when the file has sync markers one CADU apart)")
    ap.add_argument("--frame-length", type=int, default=0, metavar="BYTES",
                    help="length of the transfer frames of a file of frames (default: detected; implies "
                         "--input frames)")
    ap.add_argument("--interleave", type=int, default=5, help="RS interleaving depth I (default 5: 1279-byte CADU)")
    ap.add_argument("--randomization", choices=("auto", "yes", "no"), default="auto",
                    help="whether the codeblocks are randomized: auto (default: detected from the frames), yes, "
                         "or no (already de-randomized, as many receivers save them)")
    ap.add_argument("--not-randomized", action="store_true", help="same as --randomization no")
    ap.add_argument("--fecf", action="store_true", help="the frames end with a 2-byte FECF")
    ap.add_argument("--frames", type=int, nargs="?", const=50, default=None, metavar="N",
                    help="also list the frames: the first N (default 50; 0: all)")
    ap.add_argument("-o", "--output", help="write every frame to this CSV file")
    ap.add_argument("--show", metavar="N",
                    help="show the content of CADU number N (as in --frames): fields, packets and hex dump; "
                         "several as 5,8 or 10-12, 'last' for the last one")
    ap.add_argument("--no-dump", action="store_true", help="with --show: without the hex dump")
    ap.add_argument("--no-rs", action="store_true", help="do not check the Reed-Solomon code (faster)")
    ap.add_argument("--exclude-bad", action="store_true",
                    help="leave the frames the Reed-Solomon code cannot correct out of the analysis")
    ap.add_argument("--no-packets", action="store_true",
                    help="do not rebuild the packets (no packet and APID statistics; faster)")
    ap.add_argument("--check-apids", default=DEFAULT_CHECKED_APIDS, metavar="LIST",
                    help="APIDs whose sequence count continuity is checked, e.g. 100 or 15-16,100 (default "
                         f"{DEFAULT_CHECKED_APIDS}: time reporting and science data); the others may be filtered on board")
    ap.add_argument("--epoch", default="none", metavar="EPOCH",
                    help="epoch of the CUC packet times, to show them as dates: none (default: seconds), gps "
                         "(1980-01-06), tai (1958-01-01), unix, 2000, or a date such as 2000-01-01")
    ap.add_argument("--export-tables", metavar="FOLDER",
                    help="write the packet tables of the summary (VCs, APIDs, services, SIDs, gaps) as CSV files "
                         "to FOLDER")
    ap.add_argument("--export-packets", metavar="FOLDER",
                    help="write the packets of each APID to FOLDER/apid_NNNN.bin and their list to "
                         "FOLDER/packets.csv (idle packets left out)")
    args = ap.parse_args(argv)
    if not 1 <= args.interleave <= 8:
        ap.error("the interleaving depth must be 1 to 8")
    if args.not_randomized and args.randomization == "yes":
        ap.error("--not-randomized contradicts --randomization yes")
    if args.exclude_bad and args.no_rs:
        ap.error("--exclude-bad needs the Reed-Solomon check: drop --no-rs")
    if args.no_dump and not args.show:
        ap.error("--no-dump applies to --show only")
    try:
        checked_apids = parse_apids(args.check_apids)
    except ValueError as exc:
        ap.error(f"--check-apids: {exc}")
    try:
        args.epoch_value = parse_epoch(args.epoch)
    except ValueError:
        ap.error(f"--epoch: not an epoch: {args.epoch} (none, gps, tai, unix, 2000 or a date as 2000-01-01)")
    if args.export_tables and args.no_packets:
        ap.error("--export-tables contradicts --no-packets")
    if args.export_packets and args.no_packets:
        ap.error("--export-packets contradicts --no-packets")
    if args.frame_length and args.input == "cadu":
        ap.error("--frame-length applies to a file of transfer frames, not of CADUs")
    if args.frame_length and not 8 <= args.frame_length <= MAX_FRAME_LEN:
        ap.error(f"the frame length must be 8 to {MAX_FRAME_LEN} bytes")
    if len(args.file) > 1 and not args.concat and (args.output or args.export_packets or args.export_tables):
        ap.error("-o, --export-packets and --export-tables with several files need --concat (one stream)")

    if args.overview:
        if (args.concat or args.show or args.output or args.export_packets or args.export_tables
                or args.frames is not None):
            ap.error("--overview gives the table and the summary of each file only (no --concat, --show, --frames, "
                     "-o, --export-packets)")
        randomization = "no" if args.not_randomized else args.randomization
        items = []
        for name in args.file:
            items.append(file_overview(name, read_input(name), args.interleave, args.input, args.frame_length,
                                       randomization, args.fecf, reed_solomon=not args.no_rs,
                                       packets=not args.no_packets, report=True, checked_apids=checked_apids,
                                       exclude_bad=args.exclude_bad))
        print("\n".join(overview_lines(items) + overview_reports(items)))
        return 0
    if args.concat and len(args.file) > 1:
        data, bounds = concatenate([(os.path.basename(f), read_input(f)) for f in args.file])
        return _analyse(args, ap, f"{len(args.file)} files concatenated ({', '.join(args.file)})", data,
                        checked_apids, bounds)
    code = 0
    for k, name in enumerate(args.file):
        if k:
            print("\n" + "=" * 100 + "\n")
        code = max(code, _analyse(args, ap, name, read_input(name), checked_apids))
    return code


def _analyse(args, ap, name: str, data: bytes, checked_apids, bounds=None) -> int:
    """Summary (or --show, -o, --export-packets) of one file, or of files concatenated (``bounds``)."""
    p = prepare(data, args.interleave, args.input, args.frame_length,
                "no" if args.not_randomized else args.randomization, args.fecf)
    frame_length, length_how, randomized, how = p["frame_length"], p["length_how"], p["randomized"], p["randomized_how"]
    if frame_length and args.exclude_bad:
        ap.error("--exclude-bad needs the Reed-Solomon code: transfer frames have none")
    if args.show:  # one CADU: no need to check the whole file
        rows = decode_all(data, args.interleave, randomized, args.fecf, reed_solomon=False, frame_length=frame_length)
    else:
        rows = decode_all(data, args.interleave, randomized, args.fecf, reed_solomon=not args.no_rs,
                          frame_length=frame_length)
    if bounds:
        tag_files(rows, bounds)
    sync_len, block_len, frame_len = layout(args.interleave, frame_length)
    cadu_len = sync_len + block_len
    if frame_length:
        print(f"{name}: {len(data)} bytes; transfer frames of {frame_len} bytes ({length_how}), "
              f"no sync marker, no Reed-Solomon code; frames {how}")
    else:
        print(f"{name}: {len(data)} bytes; CADU {cadu_len} bytes, transfer frame {frame_len} "
              f"bytes (I = {args.interleave}); codeblocks {how}")
    if not rows:
        print("The file is shorter than one transfer frame." if frame_length else "No sync marker 1ACFFC1D found.")
        return 1
    if args.show:
        try:
            numbers = parse_selection(args.show, len(rows))
        except ValueError as exc:
            ap.error(str(exc))
        for number in numbers:
            print()
            print("\n".join(show_cadu(number, rows[number - 1], data, args.interleave, randomized,
                                      args.fecf, dump=not args.no_dump, reed_solomon=not args.no_rs,
                                      frame_length=frame_length)))
        return 0
    analysed = [r for r in rows if r.get("rs") != "uncorrectable"] if args.exclude_bad else rows
    packets = None if args.no_packets else packet_statistics(data, rows, args.interleave, randomized, args.fecf,
                                                             frame_length=frame_length)
    print("\n".join(summary(analysed, len(data), cadu_len, all_rows=rows, packets=packets,
                             checked_apids=checked_apids, unit="Transfer frames" if frame_length else "CADUs",
                             epoch=args.epoch_value)))
    if args.export_tables:
        names = export_tables(packets, args.export_tables, args.epoch_value)
        print(f"\nTables written to {args.export_tables}: {', '.join(names)}")

    if args.frames is not None:
        shown = analysed if args.frames == 0 else analysed[:args.frames]
        print()
        print("\n".join(frame_lines(shown)))
        if len(shown) < len(analysed):
            print(f"... {len(analysed) - len(shown)} more frames (--frames 0 for all, -o for a CSV file)")

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            writer.writeheader()
            writer.writerows(analysed)
        print(f"\nFrames written to {args.output}" + (" (uncorrectable frames excluded)"
                                                       if len(analysed) < len(rows) else ""))
    if args.export_packets:
        counts = export_packets(data, rows, args.export_packets, args.interleave, randomized, args.fecf,
                                frame_length=frame_length)
        print(f"\nPackets written to {args.export_packets}: " + (", ".join(
            f"APID {apid}: {n}" for apid, n in sorted(counts.items())) or "none") + "; list in packets.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
