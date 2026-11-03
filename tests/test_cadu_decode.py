import pytest

import cadu_decode as cd
import rs_ccsds


def _cadu(mc, vc, interleave=5, randomized=True, vcid=7, fhp=1, clcw=None):
    # Header of the reference frame: SCID 238, VC 7, OCF, segment length ID 3, FHP 1; CLCW 01 04 00 00.
    header = (bytes([0x0E, 0xE0 | (vcid << 1) | 1]) + bytes([mc, vc])
              + (0x1800 | fhp).to_bytes(2, "big"))
    frame_len = 223 * interleave
    frame = header + bytes(frame_len - 6 - 4) + (clcw or bytes.fromhex("01040000"))
    codeblock = rs_ccsds.encode_codeblock(frame, interleave)
    if randomized:
        codeblock = bytes(a ^ b for a, b in zip(codeblock, cd.pseudo_random_sequence(len(codeblock))))
    return cd.ASM + codeblock


def test_pseudo_random_sequence():
    assert cd.pseudo_random_sequence(8).hex() == "ff480ec09a0d70bc"
    assert cd.pseudo_random_sequence(510)[255:] == cd.pseudo_random_sequence(255)  # period 255


def test_decode_reference_frame():
    data = _cadu(1, 7) + _cadu(2, 8) + b"junk" + _cadu(4, 9)
    rows = list(cd.decode(data))
    assert [offset for offset, _ in rows] == [0, 1279, 2562]
    first = rows[0][1]
    assert (first["version"], first["scid"], first["vcid"], first["ocf_flag"]) == (0, 238, 7, 1)
    assert (first["mc_count"], first["vc_count"]) == (1, 7)
    assert (first["sec_header_flag"], first["sync_flag"], first["packet_order_flag"]) == (0, 0, 0)
    assert (first["segment_length_id"], first["first_header_pointer"]) == (3, 1)
    assert (first["clcw_type"], first["clcw_version"], first["clcw_status"]) == (0, 0, 0)
    assert (first["cop_in_effect"], first["clcw_vcid"], first["report_value"]) == (1, 1, 0)
    assert [r["mc_count"] for _, r in rows] == [1, 2, 4]


def test_not_randomized_and_cli(tmp_path, capsys):
    path = tmp_path / "cadu.hex"
    path.write_text((_cadu(1, 7, randomized=False) + _cadu(3, 8, randomized=False)).hex(" "))
    out = tmp_path / "frames.csv"
    assert cd.main([str(path), "--not-randomized", "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert "CADUs: 2 of 1279 bytes" in text
    assert "1 jumps, about 1 frames missing" in text
    assert out.read_text().splitlines()[1].startswith("1,0,0,238,7,1,1,7")


def test_summary_of_virtual_channels(tmp_path, capsys):
    # VC 1: counters 0, 1, 3 (one frame missing); VC 7: two idle frames; master counter 0..5 with 4 missing.
    frames = [_cadu(0, 0, vcid=1), _cadu(1, 0, vcid=7, fhp=0x7FE), _cadu(2, 1, vcid=1, fhp=0x7FF),
              _cadu(3, 1, vcid=7, fhp=0x7FE), _cadu(8, 3, vcid=1)]
    path = tmp_path / "cadu.bin"
    path.write_bytes(b"".join(frames))
    assert cd.main([str(path)]) == 0
    text = capsys.readouterr().out
    assert "CADUs: 5 of 1279 bytes" in text
    assert "Master channel counter: 0 → 8, 1 jumps, about 4 frames missing" in text
    section = text.split("Virtual channels:")[1].split("idle: FHP")[0]
    vc_lines = {line.split()[0]: line.split() for line in section.splitlines()
                if line.startswith("  ") and line.split() and line.split()[0] in ("1", "7")}
    # VC, frames, share, %, MB, idle, packet start, no header, first, last, jumps, missing, RS corrected
    assert vc_lines["1"][1:2] + vc_lines["1"][4:] == ["3", "0.00", "0", "2", "1", "0", "3", "1", "1", "0"]
    assert vc_lines["7"][1:2] + vc_lines["7"][4:] == ["2", "0.00", "2", "0", "0", "0", "1", "0", "0", "0"]
    assert cd.virtual_channel_table([{"vcid": 3, "vc_count": k, "first_header_pointer": 0x7FF}
                                     for k in range(1000)], 1279)[1][3] == "1.28"   # 1000 x 1279 bytes
    rows = [dict(offset=o, **f) for o, f in cd.decode(path.read_bytes())]
    assert cd._counter_gaps([255, 0, 2]) == (1, 1)  # wrap at 256
    assert len(cd.frame_lines(rows)) == 6


def test_show_cadu_with_packets():
    # FHP 10: a packet of APID 100 (20 bytes) then one of APID 101 running into the next frame.
    frame = bytearray(1115)
    frame[0:6] = bytes.fromhex("0EEF") + bytes([1, 7]) + (0x1800 | 10).to_bytes(2, "big")
    frame[16:22] = bytes.fromhex("0864C005") + (20 - 7).to_bytes(2, "big")
    frame[36:42] = bytes.fromhex("08654006") + (3000 - 7).to_bytes(2, "big")
    frame[-4:] = bytes.fromhex("01040000")
    data = cd.ASM + cd._xor(rs_ccsds.encode_codeblock(bytes(frame), 5), cd.pseudo_random_sequence(1275))
    packets = cd.packet_headers(bytes(frame[6:1111]), 10)
    assert [(p["apid"], p["sequence_count"], p["length"], p["continues"]) for p in packets] == [
        (100, 5, 20, False), (101, 6, 3000, True)]
    assert cd.packet_headers(bytes(10), 0x7FE) == []
    (offset, fields), = cd.decode(data)
    text = "\n".join(cd.show_cadu(1, dict(offset=offset, **fields), data, 5, True, False))
    assert "Spacecraft ID" in text and "0011101110" in text
    assert "continues in the next frame" in text
    assert "Reed-Solomon check symbols at byte 1115" in text


def test_parse_selection():
    assert cd.parse_selection("5", 10) == [5]
    assert cd.parse_selection("2,4-6,last", 10) == [2, 4, 5, 6, 10]
    with pytest.raises(ValueError):
        cd.parse_selection("11", 10)


def test_randomization_detected():
    clear = b"".join(_cadu(k, k, vcid=1 if k % 2 else 7, randomized=False) for k in range(20))
    scrambled = b"".join(_cadu(k, k, vcid=1 if k % 2 else 7) for k in range(20))
    assert cd.detect_randomization(clear) is False
    assert cd.detect_randomization(scrambled) is True
    # Read the wrong way the virtual channels come out with the top bit inverted: 1 -> 5, 7 -> 3.
    assert {f["vcid"] for _o, f in cd.decode(clear, randomized=True)} == {5, 3}


def test_unusual_clcw_listed(tmp_path, capsys):
    frames = [_cadu(k, k) for k in range(6)]
    # CADU 4: CLCW with lockout and report value 9 instead of 01 04 00 00.
    frames[3] = _cadu(3, 3, clcw=bytes.fromhex("01042009"))
    path = tmp_path / "cadu.bin"
    path.write_bytes(b"".join(frames))
    rows = [dict(offset=o, **f) for o, f in cd.decode(path.read_bytes())]
    usual = cd.usual_clcw(rows)
    assert (usual["clcw_vcid"], usual["cop_in_effect"], usual["report_value"]) == (1, 1, 0)
    assert cd.clcw_differences(rows[3], usual) == {"lockout": (0, 1), "report_value": (0, 9)}
    assert cd.main([str(path)]) == 0
    text = capsys.readouterr().out
    assert "CADUs with a different CLCW: 1" in text
    assert "lockout 0→1, report 0→9" in text


def _with_errors(cadu: bytes, positions) -> bytes:
    damaged = bytearray(cadu)
    for p in positions:
        damaged[4 + p] ^= 0xA5
    return bytes(damaged)


def test_reed_solomon_frame_error_rate(tmp_path, capsys):
    frames = [_cadu(k, k) for k in range(10)]
    frames[2] = _with_errors(frames[2], [1, 2, 3, 500])  # VC and counter bytes hit: corrected
    frames[6] = _with_errors(frames[6], range(0, 5 * 20, 5))  # 20 errors in codeword 1: uncorrectable
    data = b"".join(frames)
    rows = cd.decode_all(data)
    assert [r["rs"] for r in rows].count("ok") == 8
    assert (rows[2]["rs"], rows[2]["rs_corrected"], rows[2]["vcid"], rows[2]["mc_count"]) == ("corrected", 4, 7, 2)
    assert (rows[6]["rs"], rows[6]["rs_failed"]) == ("uncorrectable", 1)
    path = tmp_path / "cadu.bin"
    path.write_bytes(data)
    assert cd.main([str(path), "--exclude-bad"]) == 0
    text = capsys.readouterr().out
    assert "8 frames without errors, 1 corrected (4 bytes), 1 uncorrectable" in text
    assert "1/10 = 1.000e-01" in text
    assert "Uncorrectable CADUs" not in text  # excluded: not listed
    assert "Analysis below on 9 frames: 1 uncorrectable frames excluded" in text
    assert "Uncorrectable CADUs: 7" in "\n".join(cd.summary(rows, len(data), 1279))
    out = tmp_path / "frames.csv"
    assert cd.main([str(path), "--exclude-bad", "--frames", "0", "-o", str(out)]) == 0
    listed = capsys.readouterr().out
    assert "FAIL" not in listed and "corr. 4" in listed
    assert len(out.read_text().splitlines()) == 1 + 9
    shown = "\n".join(cd.show_cadu(3, rows[2], data, 5, True, False, dump=False))
    # Byte p belongs to codeword p mod 5: 500 -> 1, 1 -> 2, 2 -> 3, 3 -> 4.
    assert "1: 1 corrected, 2: 1 corrected, 3: 1 corrected, 4: 1 corrected, 5: ok" in shown
    assert "bytes corrected at offsets 1, 2, 3, 500" in shown


def test_rs_codec():
    import random

    rng = random.Random(3)
    data = bytes(rng.randrange(256) for _ in range(223))
    word = data + rs_ccsds.encode(data)
    assert rs_ccsds.decode(word) == (word, 0)
    damaged = bytearray(word)
    for p in rng.sample(range(255), 16):
        damaged[p] ^= rng.randrange(1, 256)
    assert rs_ccsds.decode(bytes(damaged)) == (word, 16)
    for p in rng.sample(range(255), 4):
        damaged[p] ^= 0x11
    assert rs_ccsds.decode(bytes(damaged))[1] == -1


def test_errors_along_the_file():
    # 40 CADUs: uncorrectable frames only at the start (1, 2) and at the end (40).
    frames = [_cadu(k, k) for k in range(40)]
    for k in (0, 1, 39):
        frames[k] = _with_errors(frames[k], range(0, 5 * 20, 5))
    rows = cd.decode_all(b"".join(frames))
    lines = cd.errors_along_file(rows)
    assert lines[2].split()[:3] == ["0-10", "%", "1-4"] and lines[2].split()[-1] == "2"  # first part: 2 FAIL
    assert lines[-2].split()[-1] == "1"  # last part: 1 FAIL
    assert "First and last 5 % (2 + 2 CADUs): 3 uncorrectable; in between: 0/36 = 0.000e+00" in lines[-1]


def _packet(apid, seq, size):
    """A Space Packet of ``size`` bytes (header included), data bytes = APID & 0xFF."""
    return ((0x0800 | apid).to_bytes(2, "big") + (0xC000 | seq).to_bytes(2, "big")
            + (size - 7).to_bytes(2, "big") + bytes([apid & 0xFF]) * (size - 6))


def _frames_from_stream(stream, vcid, mc_start, vc_start, interleave=5):
    """CADUs carrying the packet ``stream`` (list of packets) on one VC, with the First Header Pointers."""
    field_len = 223 * interleave - 6 - 4
    data, starts, pos = b"".join(stream), [], 0
    for p in stream:
        starts.append(pos)
        pos += len(p)
    cadus = []
    for k in range(0, (len(data) + field_len - 1) // field_len):
        lo, hi = k * field_len, (k + 1) * field_len
        inside = [x - lo for x in starts if lo <= x < hi]
        fhp = inside[0] if inside else 0x7FF
        field = data[lo:hi].ljust(field_len, b"\0")
        header = (bytes([0x0E, 0xE0 | (vcid << 1) | 1]) + bytes([(mc_start + k) % 256, (vc_start + k) % 256])
                  + (0x1800 | fhp).to_bytes(2, "big"))
        frame = header + field + bytes.fromhex("01040000")
        codeblock = rs_ccsds.encode_codeblock(frame, interleave)
        cadus.append(cd.ASM + bytes(a ^ b for a, b in zip(codeblock, cd.pseudo_random_sequence(len(codeblock)))))
    return cadus


def test_packets_rebuilt(tmp_path, capsys):
    # VC 1: APID 51 (123 bytes) and APID 35 (77 bytes) interleaved, APID 35 count 13405 skipped (sent on VC 0),
    # then an idle packet filling the last frame. VC 0: the APID 35 packet 13405.
    stream, seq51, seq35 = [], 3090, 13404
    for k in range(40):
        stream.append(_packet(51, seq51, 123))
        seq51 += 1
        if k % 2 == 0:
            if seq35 == 13405:
                seq35 += 1  # goes to VC 0
            stream.append(_packet(35, seq35, 77))
            seq35 += 1
    used = sum(len(p) for p in stream)
    stream.append(_packet(cd.IDLE_APID, 0, 1105 - used % 1105))
    vc1 = _frames_from_stream(stream, 1, 0, 0)
    vc0 = _frames_from_stream([_packet(35, 13405, 77), _packet(cd.IDLE_APID, 0, 1105 - 77)], 0, len(vc1), 0)
    data = b"".join(vc1 + vc0)
    rows = cd.decode_all(data)
    stats = cd.packet_statistics(data, rows)
    by = {(a["apid"], a["vc"]): a for a in stats["apids"]}
    assert by[(51, 1)]["packets"] == 40 and by[(51, 1)]["gaps"] == 0
    assert by[(35, 1)]["packets"] == 20 and by[(35, 1)]["gaps"] == 1 and by[(35, 1)]["missing"] == 1
    assert by[(35, 0)]["first_seq"] == 13405
    (gap,) = stats["gaps"]
    assert (gap["apid"], gap["vc"], gap["before"], gap["after"], gap["found_in"]) == (35, 1, 13404, 13406, [0])
    assert stats["vcs"][1]["idle_packet_bytes"] == 1105 - used % 1105
    assert stats["vcs"][1]["cut"] == 0 and stats["vcs"][1]["resyncs"] == 0

    # A frame of VC 1 lost: the packets running through it are cut, the gap is put down to the lost frame.
    lost = b"".join(vc1[:3] + vc1[4:] + vc0)
    stats = cd.packet_statistics(lost, cd.decode_all(lost))
    by = {(a["apid"], a["vc"]): a for a in stats["apids"]}
    assert stats["vcs"][1]["cut"] == 1
    assert by[(51, 1)]["gaps"] == 0 and by[(51, 1)]["gaps_lost_frames"] == 1

    path = tmp_path / "cadu.bin"
    path.write_bytes(data)
    out = tmp_path / "packets"
    assert cd.main([str(path), "--export-packets", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Packets (rebuilt from the frames" in text and "missing counts found in" not in text  # 0, 100 only
    vc1_line = next(l for l in text.splitlines() if l.startswith("   1 ") and "B (" in l).split()
    assert vc1_line[5] == f"{sum(len(p) for p in stream) / 1e6:.2f}"   # MB of the packets of VC 1
    assert vc1_line[6] == f"{sum(len(p) for p in stream[:-1]) / 1e6:.2f}"   # without the idle packet
    assert cd.main([str(path), "--check-apids", "35, 51"]) == 0
    text = capsys.readouterr().out
    assert "missing counts found in" in text and "APIDs 35, 51;" in text
    assert cd.parse_apids("15-16, 100") == {15, 16, 100}
    with pytest.raises(ValueError):
        cd.parse_apids("3000")
    assert (out / "apid_0051.bin").read_bytes() == b"".join(p for p in stream if p[1] == 51)
    assert len((out / "packets.csv").read_text().splitlines()) == 1 + 40 + 21
    assert not (out / "apid_2047.bin").exists()


def test_sequence_count_going_back():
    # APID 46: 100..119, then back to 50..59 (another stretch of data): a back jump, nothing missing.
    stream = [_packet(46, c, 100) for c in list(range(100, 120)) + list(range(50, 60))]
    data = b"".join(_frames_from_stream(stream, 1, 0, 0))
    (a,) = [a for a in cd.packet_statistics(data, cd.decode_all(data))["apids"] if a["apid"] == 46]
    assert (a["back_jumps"], a["gaps"], a["missing"], a["missing_lost_frames"]) == (1, 0, 0, 0)


def _bare(cadus, interleave=5):
    """The transfer frames of randomized CADUs, as a receiver saves them: no sync marker, de-randomized,
    without the Reed-Solomon check symbols."""
    prn = cd.pseudo_random_sequence(255 * interleave)
    return [bytes(a ^ b for a, b in zip(c[4:4 + 223 * interleave], prn)) for c in cadus]


def _idle_frame(mc, vc, length=1115):
    # As in an AWS capture: 06 8E A6 08 1F FE 5A 5A ... (SCID 104, VC 7, no OCF, idle data only)
    return bytes([0x06, 0x8E, mc % 256, vc % 256, 0x1F, 0xFE]) + b"\x5a" * (length - 6)


def test_transfer_frames_input_detected():
    cadus = _frames_from_stream([_packet(51, k, 123) for k in range(30)], 1, 0, 0)
    assert cd.detect_input(b"".join(cadus)) == "cadu"
    frames = b"".join(_bare(cadus))
    assert cd.detect_input(frames) == "frames"
    assert cd.detect_frame_length(frames) == 1115
    assert cd.detect_frame_length(b"".join(_idle_frame(k, k, 1024) for k in range(20))) == 1024
    assert cd.detect_frame_length(bytes(5000)) == 0   # no frames: not detected


def test_transfer_frames_decoded_as_the_cadus():
    stream = [_packet(51, k, 123) for k in range(30)] + [_packet(35, k, 77) for k in range(10)]
    cadus = _frames_from_stream(stream, 1, 0, 0)
    frames = b"".join(_bare(cadus))
    rows = cd.decode_all(frames, randomized=False, frame_length=1115)
    assert [r["offset"] for r in rows] == [k * 1115 for k in range(len(cadus))]
    assert all("rs" not in r for r in rows)   # no Reed-Solomon code in transfer frames
    ref_rows = cd.decode_all(b"".join(cadus))
    keys = ("scid", "vcid", "mc_count", "vc_count", "first_header_pointer", "report_value")
    assert [[r[k] for k in keys] for r in rows] == [[r[k] for k in keys] for r in ref_rows]
    stats = cd.packet_statistics(frames, rows, randomized=False, frame_length=1115)
    ref = cd.packet_statistics(b"".join(cadus), ref_rows)
    assert stats["apids"] == ref["apids"] and stats["vcs"] == ref["vcs"]
    assert not cd.detect_randomization(frames, frame_length=1115)


def test_transfer_frames_cli(tmp_path, capsys):
    path = tmp_path / "frames.bin"
    path.write_bytes(b"".join(_idle_frame(0xA6 + k, 8 + k) for k in range(12)) + b"\x06\x8e")
    assert cd.main([str(path)]) == 0
    text = capsys.readouterr().out
    assert "transfer frames of 1115 bytes (detected), no sync marker, no Reed-Solomon code" in text
    assert "Transfer frames: 12 of 1115 bytes" in text and "2 bytes of the file are in no transfer frame: 2 at the end" in text
    assert "Reed-Solomon: none" in text and "Spacecraft IDs: 104 (12 frames)" in text
    assert cd.main([str(path), "--show", "2", "--no-dump"]) == 0
    text = capsys.readouterr().out
    assert "Transfer frame #2 at byte 1115" in text and "Sync marker" not in text
    assert "idle data only" in text and "codewords" not in text
    assert cd.main([str(path), "--frame-length", "1115", "--frames"]) == 0
    assert "(option)" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cd.main([str(path), "--input", "frames", "--exclude-bad"])
    with pytest.raises(SystemExit):
        cd.main([str(path), "--input", "cadu", "--frame-length", "1115"])


def test_several_files_one_summary_each_or_concatenated(tmp_path, capsys):
    stream = [_packet(100, k, 300) for k in range(60)]
    whole = b"".join(_frames_from_stream(stream, 1, 0, 0))
    cuts = [0, 3000, 9000, len(whole)]   # chunks cut in the middle of CADUs
    paths = []
    for k in range(3):
        paths.append(tmp_path / f"chunk_{k}.bin")
        paths[-1].write_bytes(whole[cuts[k]:cuts[k + 1]])

    # One summary per file: the CADUs cut at the boundaries are lost in each file alone
    assert cd.main([str(p) for p in paths]) == 0
    text = capsys.readouterr().out
    assert text.count("CADUs: ") == 3 and "chunk_0.bin: 3000 bytes" in text and "=" * 100 in text

    # Concatenated: the same as the whole capture, and each frame says where it comes from
    data, bounds = cd.concatenate([(p.name, p.read_bytes()) for p in paths])
    assert data == whole and bounds == [(0, "chunk_0.bin"), (3000, "chunk_1.bin"), (9000, "chunk_2.bin")]
    rows = cd.decode_all(data)
    cd.tag_files(rows, bounds)
    assert [(r["file"], r["file_offset"]) for r in rows[:4]] == [
        ("chunk_0.bin", 0), ("chunk_0.bin", 1279), ("chunk_0.bin", 2558), ("chunk_1.bin", 837)]
    assert cd.position_text(rows[3]) == "byte 837 of chunk_1.bin"
    assert cd.packet_statistics(data, rows)["apids"] == cd.packet_statistics(whole, cd.decode_all(whole))["apids"]
    assert cd.main([str(p) for p in paths] + ["--concat", "--frames", "5"]) == 0
    text = capsys.readouterr().out
    assert "3 files concatenated" in text and f"CADUs: {len(whole) // 1279}" in text
    assert "Files concatenated (frames starting in each): chunk_0.bin 3, chunk_1.bin 5" in text
    assert "file offset" in text
    assert cd.main([str(p) for p in paths] + ["--concat", "--show", "4", "--no-dump"]) == 0
    assert "CADU #4 at byte 837 of chunk_1.bin" in capsys.readouterr().out
    with pytest.raises(SystemExit):   # one CSV for several summaries: ambiguous
        cd.main([str(p) for p in paths] + ["-o", str(tmp_path / "x.csv")])


def test_pass_from_name():
    from datetime import datetime
    p = cd.pass_from_name(r"C:\data\AWS1_H_1_20261028T150735_20261028T145625_20261028T150732_SVA.bin")
    assert p == {"satellite": "AWS1", "station": "SVA", "start": datetime(2026, 10, 28, 14, 56, 25),
                 "end": datetime(2026, 10, 28, 15, 7, 32)}
    assert cd.pass_from_name("capture_20261028T145625_20261028T150732.bin")["end"].minute == 7
    assert cd.pass_from_name("capture.bin") == {}


def test_overview_one_row_per_file(tmp_path, capsys):
    late = tmp_path / "AWS1_H_1_20261029T101000_20261029T095000_20261029T100500_KIR.bin"
    early = tmp_path / "AWS1_H_1_20261028T150735_20261028T145625_20261028T150732_SVA.bin"
    late.write_bytes(b"".join(_frames_from_stream([_packet(100, k, 300) for k in range(30)], 1, 0, 0)))
    early.write_bytes(b"".join(_bare(_frames_from_stream([_packet(100, k, 300) for k in range(20)], 1, 0, 0))
                               + [_idle_frame(k, k) for k in range(8, 12)]))
    assert cd.main([str(late), str(early), "--overview"]) == 0
    lines = capsys.readouterr().out.splitlines()
    rows = [l for l in lines if "AWS1_H_1" in l]
    assert "SVA" in rows[0] and "2026-10-28 14:56:25" in rows[0] and "15:07:32" in rows[0]   # by pass start
    assert "11.1" in rows[0] and "TF 1115 B" in rows[0] and "1:6 7:4" in rows[0]   # minutes, input, VCs
    assert "KIR" in rows[1] and "CADU 1279 B" in rows[1]
    # then the full summary of each file, in the same order
    titles = [k for k, l in enumerate(lines) if l.startswith("AWS1_H_1_")]
    assert len(titles) == 2 and "SVA" in lines[titles[0]] and "pass 2026-10-28 14:56:25 → 15:07:32" in lines[titles[0]]
    first = "\n".join(lines[titles[0]:titles[1]])
    assert "Transfer frames: 10 of 1115 bytes" in first and "Packets (rebuilt" in first
    assert "CADUs: 9 of 1279 bytes" in "\n".join(lines[titles[1]:])
    total = next(l for l in lines if "2 files" in l).split()
    assert total[:2] == ["total", "19"]   # frames: 10 + 9 (6 + 4 + 9 CADUs)
    o = cd.file_overview(str(late), late.read_bytes())
    assert (o["frames"], o["uncorrectable"], o["vcs"]) == (9, 0, {1: 9}) and o["packets"] >= 30
    with pytest.raises(SystemExit):
        cd.main([str(late), str(early), "--overview", "--concat"])


def _pus_packet(apid, seq, size, service, subtype, seconds, sid=None):
    """A PUS-C TM packet of ``size`` bytes: data field header with P-field 2F (CUC 4 + 3 bytes), then the SID."""
    head = (bytes([0x23, service, subtype, 0, 0, 0, 0, 0x2F]) + seconds.to_bytes(4, "big") + bytes(3)
            + (sid.to_bytes(2, "big") if sid is not None else b""))
    body = head + bytes(size - 6 - len(head))
    return (0x0800 | apid).to_bytes(2, "big") + (0xC000 | seq).to_bytes(2, "big") + (len(body) - 1).to_bytes(2, "big") + body


def test_pus_fields_of_a_real_hk_packet():
    # Start of a packet of an AWS capture: APID 46, PUS-C 3,25, CUC 580D2A71 A40F00, SID 5.
    head = bytes.fromhex("082ef1c900ff2303190000 00002f580d2a71a40f00 0005 01b8ff".replace(" ", ""))
    pus = cd.pus_fields(head)
    assert (pus["pus"], pus["service"], pus["subtype"], pus["sid"], pus["sid_offset"]) == (2, 3, 25, 5, 21)
    assert pus["time"] == 0x580D2A71 + 0xA40F00 / (1 << 24)
    other = bytes.fromhex("080ffab8009d23020900000000 2f580d2a73b63400".replace(" ", ""))
    assert cd.pus_fields(other) == {"pus": 2, "service": 2, "subtype": 9, "time": 0x580D2A73 + 0xB63400 / (1 << 24),
                                    "sid_offset": 21}   # 2,9: no SID
    assert cd.pus_fields(_packet(51, 0, 40)) == {}       # no secondary header


def test_sids_of_the_hk_reports(tmp_path, capsys):
    stream = []
    for t in range(64):
        stream.append(_pus_packet(46, t, 264, 3, 25, 1000 + t, sid=5))              # every second
        if t % 16 == 0:
            stream.append(_pus_packet(36, t // 16, 266, 3, 25, 1000 + t, sid=3))   # every 16 s
        stream.append(_pus_packet(15, t, 164, 2, 9, 1000 + t))
    data = b"".join(_frames_from_stream(stream, 1, 0, 0))
    stats = cd.packet_statistics(data, cd.decode_all(data))
    sids = {(h["apid"], h["sid"]): h for h in stats["sids"]}
    assert set(sids) == {(46, 5), (36, 3)}
    assert (sids[(46, 5)]["packets"], sids[(46, 5)]["period"], sids[(46, 5)]["per_day"]) == (64, 1, 86400)
    assert (sids[(36, 3)]["packets"], sids[(36, 3)]["period"], sids[(36, 3)]["max_length"]) == (4, 16, 266)
    assert sids[(46, 5)]["mb_per_day"] == 86400 * 264 / 1e6
    by = {a["apid"]: a for a in stats["apids"]}
    assert by[15]["services"] == {(2, 9): 64} and by[46]["services"] == {(3, 25): 64}

    path = tmp_path / "cadu.bin"
    path.write_bytes(data)
    assert cd.main([str(path), "--export-packets", str(tmp_path / "out")]) == 0
    text = capsys.readouterr().out
    section = text.split("Housekeeping reports by SID")[1]
    rows = {tuple(line.split()[:3]) for line in section.splitlines()[2:4]}
    assert rows == {("36", "3,25", "3"), ("46", "3,25", "5")}
    assert "2 SIDs" in section
    listed = (tmp_path / "out" / "packets.csv").read_text().splitlines()
    assert listed[0].endswith("service,subtype,sid,time")
    assert listed[1].split(",")[8:11] == ["3", "25", "5"]

    shown = "\n".join(cd.show_cadu(1, cd.decode_all(data)[0], data, 5, True, False, dump=False))
    assert "service  SID" in shown and " 3,25    5" in shown


def test_packets_by_service(capsys, tmp_path):
    stream = []
    for t in range(60):
        stream.append(_pus_packet(46, t, 264, 3, 25, 1000 + t, sid=5))      # 3,25 every second
        stream.append(_pus_packet(15, 2 * t, 164, 2, 9, 1000 + t))          # 2,9 twice a second
        stream.append(_pus_packet(15, 2 * t + 1, 100, 2, 9, 1000 + t))
        if t % 20 == 0:
            stream.append(_pus_packet(30, t, 40, 5, 1, 1000 + t))           # sporadic event
    stream.append(_packet(0, 0, 16))                                          # time report, no PUS header
    data = b"".join(_frames_from_stream(stream, 1, 0, 0))
    stats = cd.packet_statistics(data, cd.decode_all(data))
    assert stats["span"] == 59
    by = {(v["apid"], v["service"], v["subtype"]): v for v in stats["services"]}
    hk, dev, ev = by[(46, 3, 25)], by[(15, 2, 9)], by[(30, 5, 1)]
    assert (hk["packets"], hk["bytes"], hk["period"]) == (60, 60 * 264, 1)
    assert (dev["packets"], dev["bytes"], dev["min_length"], dev["max_length"]) == (120, 60 * 264, 100, 164)
    assert dev["bytes_per_s"] == 60 * 264 / 59 and ev["packets"] == 3 and ev["period"] == 20
    assert by[(0, None, None)]["period"] is None                              # no time: no period
    assert abs(sum(v["share"] for v in stats["services"]) - 1) < 1e-9
    totals = cd.service_totals(stats["services"])
    assert [(t["service"], t["subtype"]) for t in totals][:2] in ([(3, 25), (2, 9)], [(2, 9), (3, 25)])

    path = tmp_path / "cadu.bin"
    path.write_bytes(data)
    assert cd.main([str(path)]) == 0
    text = capsys.readouterr().out.split("Packets by PUS service")[1].split("Housekeeping reports by SID")[0]
    assert "rates over the 59 s" in text and "By APID and service:" in text
    assert "covered by the periodic packets" in text
    assert "packet times 1000.000 s to 1059.000 s" in text
    assert any(line.split()[:2] == ["2,9", "15"] for line in text.splitlines())


def test_bytes_in_no_cadu_are_reported():
    c = [_cadu(k, k) for k in range(6)]
    data = c[0] + c[1] + b"\x00" * 5000 + c[2] + c[3] + b"\x11" * 1279 + c[4] + c[5] + b"xy"
    rows = cd.decode_all(data)
    info = cd.unaccounted_bytes(rows, len(data), 1279)
    assert (info["lead"], info["tail"], info["total"]) == (0, 2, 6281)
    assert info["gaps"] == [(2 * 1279, 5000), (4 * 1279 + 5000, 1279)]
    text = "\n".join(cd.summary(rows, len(data), 1279))
    assert "6281 bytes of the file are in no cadu: 6279 in 2 stretches between them (first at byte 2558), 2 at " \
           "the end" in text


def test_master_counter_with_one_vc_only():
    rows = cd.decode_all(b"".join(_cadu(2 * k, k, vcid=3) for k in range(10)))
    text = "\n".join(cd.summary(rows, 10 * 1279, 1279))
    assert "about 9 frames missing" in text and "Note: only VC 3 is in the file" in text
    both = cd.decode_all(b"".join(_cadu(2 * k + v, k, vcid=v + 1) for k in range(5) for v in (0, 1)))
    assert "only VC" not in "\n".join(cd.summary(both, 10 * 1279, 1279))


def test_rates_ignore_breaks_in_the_times():
    stream = [_pus_packet(46, k, 100, 3, 25, 1000 + k, sid=5) for k in range(100)]
    stream.append(_pus_packet(46, 100, 100, 3, 25, 1000 + 86400 * 30, sid=5))   # a wrong time, 30 days later
    data = b"".join(_frames_from_stream(stream, 1, 0, 0))
    stats = cd.packet_statistics(data, cd.decode_all(data))
    assert (stats["span"], stats["outside"]) == (99, 1)   # the packet with the wrong time is left out
    (hk,) = [v for v in stats["services"] if v["service"] == 3]
    assert hk["bytes_per_s"] == 101 * 100 / 99
    assert cd.time_coverage([]) == (None, 0) and cd.time_coverage([5, 5]) == (None, 0)


def test_epoch_and_time_text():
    assert cd.parse_epoch("gps") == cd.datetime(1980, 1, 6) and cd.parse_epoch("none") is None
    assert cd.parse_epoch("2000-01-01") == cd.datetime(2000, 1, 1)
    assert cd.time_text(86400 + 0.25, cd.EPOCHS["gps"]) == "1980-01-07 00:00:00.250"
    assert cd.time_text(12.5) == "12.500 s" and cd.time_text(None) == "–"


def test_export_tables(tmp_path, capsys):
    stream = []
    for t in range(30):
        stream.append(_pus_packet(46, t, 264, 3, 25, 1000 + t, sid=5))
        stream.append(_pus_packet(15, t, 164, 2, 9, 1000 + t))
    path = tmp_path / "cadu.bin"
    path.write_bytes(b"".join(_frames_from_stream(stream, 1, 0, 0)))
    out = tmp_path / "tables"
    assert cd.main([str(path), "--export-tables", str(out), "--epoch", "gps"]) == 0
    assert "Tables written to" in capsys.readouterr().out
    assert sorted(p.name for p in out.iterdir()) == ["apids.csv", "gaps.csv", "service_totals.csv", "services.csv",
                                                      "sids.csv", "vcs.csv"]
    sids = (out / "sids.csv").read_text().splitlines()
    assert sids[0].startswith("apid,service,subtype,sid") and sids[1].startswith("46,3,25,5,1,30,")
    assert "1980-01-06 00:16:40.000" in sids[1]   # first packet time 1000 s from the GPS epoch
    totals = (out / "service_totals.csv").read_text()
    assert "\n2,9,15,30," in totals and "\n3,25,46,30," in totals   # service and subtype in two columns


def test_station_block_after_every_cadu():
    block = bytes(48)
    data = b"".join(_cadu(k, k) + block for k in range(5))
    rows = cd.decode_all(data)
    assert len(rows) == 5
    text = cd.unaccounted_text(cd.unaccounted_bytes(rows, len(data), 1279))
    assert text.startswith("every cadu is followed by 48 bytes (240 in all)")


def test_events_of_an_earlier_orbit_do_not_stretch_the_rates():
    stream = []
    for k in range(40):                                   # an earlier orbit: events only, every 30 s
        stream.append(_pus_packet(35, k, 80, 5, 1, k * 30))
    for k in range(600):                                  # this orbit: HK every second, 2,9 every second
        stream.append(_pus_packet(46, k, 262, 3, 25, 5000 + k, sid=5))
        stream.append(_pus_packet(15, k, 164, 2, 9, 5000 + k))
    data = b"".join(_frames_from_stream(stream, 1, 0, 0))
    stats = cd.packet_statistics(data, cd.decode_all(data))
    assert (stats["span"], stats["outside"]) == (599, 40)
    total = sum(v["bytes"] for v in stats["services"])
    assert abs(sum(v["bytes_per_s"] for v in stats["services"]) - total / 599) < 1e-6
    assert "40 packets (e.g. events of an earlier orbit) have times outside it" in cd.coverage_text(stats)
