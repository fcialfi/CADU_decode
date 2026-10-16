import cadu_decode as cd


def _cadu(mc, vc, interleave=5, randomized=True):
    # Header of the reference frame: SCID 238, VC 7, OCF, segment length ID 3, FHP 1; CLCW 01 04 00 00.
    header = bytes.fromhex("0EEF") + bytes([mc, vc]) + bytes.fromhex("1801")
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
    assert "2 CADUs of 1279 bytes" in text
    assert "Master channel counter gaps: 1" in text
    assert out.read_text().splitlines()[1].startswith("0,0,238,7,1,1,7")
