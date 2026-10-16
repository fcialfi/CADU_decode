"""Graphical view of one CADU, for the Frames tab of cadu_gui.py (Tkinter only).

The structure of the CADU as a bar, the primary header and CLCW as bit fields, the Reed-Solomon
codewords, the Space Packets of the data field and a hex dump coloured by section.
"""

import tkinter as tk
from tkinter import ttk

import cadu_decode as cd
import rs_ccsds
from summary_view import ACCENT, BAD, BG, BORDER, CARD, GOOD, MUTED, TEXT, WARN, Page

# Section colours (fill, text).
SECTION_COLORS = {
    "sync": ("#5b4a9e", "#ffffff"),
    "header": ("#2f6fb0", "#ffffff"),
    "secondary": ("#5a8fc7", "#ffffff"),
    "data": ("#dce8f5", TEXT),
    "ocf": ("#2e8540", "#ffffff"),
    "fecf": ("#1b7f86", "#ffffff"),
    "rs": ("#8a96a3", "#ffffff"),
}
DUMP_COLORS = {"header": "#d6e6f7", "secondary": "#e3eefa", "ocf": "#d8f0dc", "fecf": "#d4eff1", "rs": "#e6e9ed"}
CORRECTED = "#ffd27a"

HEADER_SHORT = {"version": "Version", "scid": "Spacecraft ID", "vcid": "VC ID", "ocf_flag": "OCF",
                "mc_count": "MC count", "vc_count": "VC count", "sec_header_flag": "Sec. hdr",
                "sync_flag": "Sync", "packet_order_flag": "Order", "segment_length_id": "Seg. ID",
                "first_header_pointer": "First Header Pointer"}
CLCW_LABELS = {"clcw_type": "Type", "clcw_version": "Version", "clcw_status": "Status", "cop_in_effect": "COP",
               "clcw_vcid": "VC ID", "no_rf_available": "No RF", "no_bit_lock": "No lock", "lockout": "Lockout",
               "wait": "Wait", "retransmit": "Retx", "farm_b_counter": "FARM-B", "report_value": "Report value"}


class DetailView(Page):
    """One CADU in detail; ``show`` fills it."""

    def __init__(self, parent):
        super().__init__(parent)
        self._message("Select a frame to see its content.")
        self.text_lines = []

    def _badge(self, parent, text, color):
        tk.Label(parent, text=f" {text} ", bg=color, fg="#ffffff", font=self.f_small).pack(side=tk.LEFT, padx=(0, 6))

    def show(self, number, row, data, interleave, randomized, fecf, reed_solomon=True, dump=True, usual=None):
        self._clear()
        self.canvas.yview_moveto(0)
        cadu, raw_block = cd.codeblock_at(data, row["offset"], interleave, randomized)
        block = raw_block
        counts = None
        if reed_solomon:
            block, counts = rs_ccsds.codeblock_status(raw_block, interleave)
        frame_len = cd.RS_K * interleave
        frame = block[:frame_len]
        fields = cd.parse_frame(frame, fecf)
        corrected = {i for i in range(len(block)) if block[i] != raw_block[i]}
        end = frame_len - (2 if fecf else 0) - (4 if fields["ocf_flag"] else 0)
        start = 6 + ((frame[6] & 0x3F) + 1 if fields["sec_header_flag"] else 0)
        self.text_lines = cd.show_cadu(number, row, data, interleave, randomized, fecf, dump=dump,
                                       reed_solomon=reed_solomon)

        # Title line.
        head = tk.Frame(self.page, bg=BG)
        head.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(head, text=f"CADU #{number}", bg=BG, fg=TEXT, font=self.f_value).pack(side=tk.LEFT)
        tk.Label(head, text=f"   byte {row['offset']:,} of the file   ·   VC {fields['vcid']}   ·   "
                 f"MC count {fields['mc_count']}   ·   VC count {fields['vc_count']}   ·   "
                 f"{cd.first_header_pointer_text(fields['first_header_pointer'])} (FHP)",
                 bg=BG, fg=MUTED, font=self.f_small).pack(side=tk.LEFT, pady=(8, 0))
        badges = tk.Frame(self.page, bg=BG)
        badges.pack(fill=tk.X, padx=16, pady=(6, 0))
        if counts is None:
            self._badge(badges, "Reed-Solomon not checked", MUTED)
        elif any(c < 0 for c in counts):
            self._badge(badges, "UNCORRECTABLE: fields may be wrong", BAD)
        elif corrected:
            self._badge(badges, f"{len(corrected)} bytes corrected", WARN)
        else:
            self._badge(badges, "no errors", GOOD)
        self._badge(badges, "de-randomized" if randomized else "not randomized", ACCENT)
        if cadu[:4] != cd.ASM:
            self._badge(badges, f"sync marker {cadu[:4].hex().upper()}", BAD)
        tk.Button(badges, text="Copy as text", relief="flat", bg=CARD, fg=TEXT, highlightthickness=1,
                  highlightbackground=BORDER, command=self._copy).pack(side=tk.RIGHT)

        # Structure.
        sections = [("sync", "Sync marker", -4, 0)]
        sections.append(("header", "Primary header", 0, 6))
        if start > 6:
            sections.append(("secondary", "Sec. header", 6, start))
        sections.append(("data", "Data field", start, end))
        if fields["ocf_flag"]:
            sections.append(("ocf", "OCF / CLCW", end, end + 4))
        if fecf:
            sections.append(("fecf", "FECF", frame_len - 2, frame_len))
        sections.append(("rs", "Reed-Solomon check symbols", frame_len, len(block)))
        self._section("Structure", f"{len(cadu)} bytes; byte numbers from the start of the transfer frame "
                                   f"({frame_len} bytes)")
        card = self._card()
        card.pack(fill=tk.X, padx=16)
        bar = tk.Canvas(card, height=70, bg=CARD, highlightthickness=0)
        bar.pack(fill=tk.X, padx=6, pady=6)
        bar.bind("<Configure>", lambda _e: self._draw_structure(bar, sections))

        # Primary header.
        self._section("Primary header", f"bytes 0-5: {frame[:6].hex(' ').upper()}")
        card = self._card()
        card.pack(fill=tk.X, padx=16)
        header_fields = [(key, HEADER_SHORT[key], bits, fields[key]) for key, _name, bits in cd.PRIMARY_HEADER_FIELDS]
        canvas = tk.Canvas(card, height=96, bg=CARD, highlightthickness=0)
        canvas.pack(fill=tk.X, padx=6, pady=6)
        canvas.bind("<Configure>", lambda _e: self._draw_fields(canvas, header_fields, set()))

        # CLCW.
        if fields["ocf_flag"]:
            diff = set(cd.clcw_differences(fields, usual)) if usual else set()
            note = f"bytes {end}-{end + 3}: {frame[end:end + 4].hex(' ').upper()}"
            if diff:
                note += "   ·   orange: different from the most frequent CLCW"
            self._section("CLCW (Operational Control Field)", note)
            card = self._card()
            card.pack(fill=tk.X, padx=16)
            clcw_fields = [(key, CLCW_LABELS.get(key, "Spare"), bits, fields[key] if key else
                            (int.from_bytes(frame[end:end + 4], "big") >> self._shift(cd.CLCW_FIELDS, i)) &
                            ((1 << bits) - 1))
                           for i, (key, _name, bits) in enumerate(cd.CLCW_FIELDS)]
            canvas2 = tk.Canvas(card, height=96, bg=CARD, highlightthickness=0)
            canvas2.pack(fill=tk.X, padx=6, pady=6)
            canvas2.bind("<Configure>", lambda _e: self._draw_fields(canvas2, clcw_fields, diff))

        # Reed-Solomon.
        if counts is not None:
            self._section("Reed-Solomon codewords", f"{interleave} interleaved: codeword i has the bytes i, i + "
                                                    f"{interleave}, i + {2 * interleave}, ...")
            words = tk.Frame(self.page, bg=BG)
            words.pack(fill=tk.X, padx=16)
            for i, c in enumerate(counts):
                color, text = (BAD, "uncorrectable") if c < 0 else (WARN, f"{c} corrected") if c else (GOOD, "ok")
                box = tk.Frame(words, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
                box.pack(side=tk.LEFT, padx=(0, 8))
                tk.Label(box, text=f"Codeword {i + 1}", bg=CARD, fg=MUTED, font=self.f_small).pack(padx=12, pady=(6, 0))
                tk.Label(box, text=text, bg=CARD, fg=color, font=self.f_title).pack(padx=12, pady=(0, 6))

        # Packets.
        fhp = fields["first_header_pointer"]
        data_field = frame[start:end]
        if fhp == 0x7FE:
            self._section("Data field", f"bytes {start}-{end - 1}: idle data only (FHP 7FE)")
        elif fhp == 0x7FF:
            self._section("Data field", f"bytes {start}-{end - 1}: no packet starts here (FHP 7FF): part of a "
                                        "longer packet")
        elif fhp >= len(data_field):
            self._section("Data field", f"First Header Pointer {fhp} beyond the data field: not valid")
        else:
            packets = cd.packet_headers(data_field, fhp)
            note = f"bytes {start}-{end - 1}"
            if fhp:
                note += f"; data field bytes 0-{fhp - 1}: end of a packet started in a previous frame"
            self._section(f"Space Packets starting in this frame ({len(packets)})", note)
            self._table((("At byte", 70), ("Version", 65), ("Type", 50), ("Sec. hdr", 70), ("APID", 60),
                         ("Seq. flags", 80), ("Seq. count", 85), ("Length", 70), ("", 220)),
                        [(start + p["offset"], "", "", "", "", "", "", "", "header continues in the next frame")
                         if "apid" not in p else
                         (start + p["offset"], p["version"], p["type"], p["sec_header_flag"], p["apid"],
                          f"{p['sequence_flags']:02b}", p["sequence_count"], p["length"],
                          "continues in the next frame" if p["continues"] else "")
                         for p in packets], anchors={8: "w"})

        # Hex dump.
        if dump:
            self._section("Hex dump", "transfer frame and check symbols" + (", corrected bytes in orange"
                                                                           if corrected else ""))
            self._legend(sections, bool(corrected))
            card = self._card()
            card.pack(fill=tk.X, padx=16, pady=(4, 16))
            self._dump(card, block, sections, corrected)

    @staticmethod
    def _shift(fields, index):
        return 32 - sum(bits for _k, _n, bits in fields[:index + 1])

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append("\n".join(self.text_lines))

    def _draw_structure(self, c, sections):
        """The sections of the CADU as blocks: proportional to their size, at least wide enough to read."""
        c.delete("all")
        w = c.winfo_width() - 4
        if w < 200:
            return
        sizes = [b - a for _k, _l, a, b in sections]
        minimum = 78
        widths = [float(minimum)] * len(sections)
        free = w - minimum * len(sections)
        big = [k for k, size in enumerate(sizes) if size / sum(sizes) * w > minimum]
        for k in big:  # the large sections share the rest in proportion
            widths[k] += free * sizes[k] / sum(sizes[i] for i in big)
        x = 2
        for (key, label, a, b), width in zip(sections, widths):
            fill, fg = SECTION_COLORS[key]
            c.create_rectangle(x, 4, x + width - 3, 44, fill=fill, outline="")
            for text in (label, label.split(" / ")[0].split()[0], ""):
                if self.f_small.measure(text) < width - 10:
                    break
            c.create_text(x + width / 2, 24, text=text, fill=fg, font=self.f_small)
            span = f"{a}-{b - 1} ({b - a} B)" if key != "sync" else f"{b - a} B"
            if self.f_small.measure(span) > width - 6:
                span = f"{b - a} B"
            c.create_text(x + width / 2, 58, text=span, fill=MUTED, font=self.f_small)
            x += width

    def _draw_fields(self, c, fields, highlight):
        """Bit-field boxes: name, number of bits, binary, value in decimal and hex."""
        c.delete("all")
        w = c.winfo_width() - 4
        if w < 100:
            return
        weights = [max(bits, 3.5) for _k, _n, bits, _v in fields]
        x = 2
        for (key, name, bits, value), weight in zip(fields, weights):
            width = weight / sum(weights) * w
            fill = "#fff1d6" if key in highlight else ("#f5f7fa" if key else "#fafbfc")
            c.create_rectangle(x, 2, x + width - 3, 92, fill=fill, outline=BORDER)
            title = name if width > len(name) * 6.5 else name.split()[0][:max(3, int(width / 7))]
            c.create_text(x + width / 2, 14, text=title, fill=MUTED, font=self.f_small)
            c.create_text(x + width / 2, 30, text=f"{bits} bit" + ("s" if bits > 1 else ""), fill=MUTED,
                          font=self.f_small)
            binary = format(value, f"0{bits}b")
            if len(binary) * 7.5 > width:
                binary = binary[:max(1, int(width / 7.5) - 1)] + "…"
            c.create_text(x + width / 2, 50, text=binary, fill=TEXT, font=self.f_mono)
            c.create_text(x + width / 2, 72, text=str(value), fill=TEXT if key else MUTED, font=self.f_title)
            if bits > 4 and width > 60:
                c.create_text(x + width / 2, 86, text=f"0x{value:0{(bits + 3) // 4}X}", fill=MUTED,
                              font=self.f_small)
            x += width

    def _legend(self, sections, corrected):
        box = tk.Frame(self.page, bg=BG)
        box.pack(fill=tk.X, padx=16, pady=(0, 2))
        for key, label, _a, _b in sections:
            if key in DUMP_COLORS:
                tk.Label(box, text=f"  {label}  ", bg=DUMP_COLORS[key], fg=TEXT, font=self.f_small).pack(
                    side=tk.LEFT, padx=(0, 6))
        if corrected:
            tk.Label(box, text="  corrected byte  ", bg=CORRECTED, fg=TEXT, font=self.f_small).pack(side=tk.LEFT)

    def _dump(self, parent, block, sections, corrected):
        lines = (len(block) + 15) // 16
        text = tk.Text(parent, height=min(lines, 40), wrap="none", font=self.f_mono, relief="flat", bg=CARD,
                       padx=10, pady=6)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.X)
        section_of = {}
        for key, _label, a, b in sections:
            if key in DUMP_COLORS:
                for i in range(max(a, 0), b):
                    section_of[i] = key
        for key, color in DUMP_COLORS.items():
            text.tag_configure(key, background=color)
        text.tag_configure("corrected", background=CORRECTED)
        text.tag_configure("offset", foreground=MUTED)
        for line in range(lines):
            base = line * 16
            text.insert(tk.END, f"{base:5d}  ", "offset")
            for i in range(base, min(base + 16, len(block))):
                tags = ("corrected",) if i in corrected else ((section_of[i],) if i in section_of else ())
                text.insert(tk.END, f"{block[i]:02X}", tags)
                text.insert(tk.END, " ")
            text.insert(tk.END, "\n")
        text.configure(state="disabled")
