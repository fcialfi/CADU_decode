import cadu_decode as cd


def _cadu(mc, vc, interleave=5, randomized=True, vcid=7, fhp=1):
    # Header of the reference frame: SCID 238, VC 7, OCF, segment length ID 3, FHP 1; CLCW 01 04 00 00.
    header = (bytes([0x0E, 0xE0 | (vcid << 1) | 1]) + bytes([mc, vc])
              + (0x1800 | fhp).to_bytes(2, "big"))
    frame_len = 223 * interleave
    frame = header + bytes(frame_len - 6 - 4) + bytes.fromhex("01040000")
    codeblock = frame + bytes(32 * interleave)  # check symbols: not checked by the decoder
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
    assert out.read_text().splitlines()[1].startswith("0,0,238,7,1,1,7")


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
    vc_lines = {line.split()[0]: line.split() for line in text.splitlines()
                if line.startswith("  ") and line.split()[0] in ("1", "7")}
    # VC, frames, share, %, idle, packet start, no header, first, last, jumps, missing
    assert vc_lines["1"][1:2] + vc_lines["1"][4:] == ["3", "0", "2", "1", "0", "3", "1", "1"]
    assert vc_lines["7"][1:2] + vc_lines["7"][4:] == ["2", "2", "0", "0", "0", "1", "0", "0"]
    rows = [dict(offset=o, **f) for o, f in cd.decode(path.read_bytes())]
    assert cd._counter_gaps([255, 0, 2]) == (1, 1)  # wrap at 256
    assert len(cd.frame_lines(rows)) == 6
