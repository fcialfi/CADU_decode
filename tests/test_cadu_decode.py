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
    # VC, frames, share, %, idle, packet start, no header, first, last, jumps, missing, RS corrected
    assert vc_lines["1"][1:2] + vc_lines["1"][4:] == ["3", "0", "2", "1", "0", "3", "1", "1", "0"]
    assert vc_lines["7"][1:2] + vc_lines["7"][4:] == ["2", "2", "0", "0", "0", "1", "0", "0", "0"]
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
