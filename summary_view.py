"""Graphical summary of a file of CADUs, for the Summary tab of cadu_gui.py (Tkinter only).

Key figures as tiles, the Reed-Solomon errors along the file as a chart, and the virtual channels,
unusual CLCWs and uncorrectable frames as tables (double click: open the CADU).
"""

import tkinter as tk
from collections import Counter
from tkinter import font as tkfont, ttk

import cadu_decode as cd

BG = "#f4f6f9"
CARD = "#ffffff"
BORDER = "#d5dbe5"
TEXT = "#1f2933"
MUTED = "#66788a"
ACCENT = "#2f6fb0"
GOOD = "#2e8540"
BAD = "#c0392b"
WARN = "#c77c02"
EDGE = "#eef1f6"


def _fmt_rate(x: float) -> str:
    return "0" if x == 0 else f"{x:.2e}"


class Page(ttk.Frame):
    """Scrollable page with the building blocks of the views: sections, cards, tiles, tables."""

    def __init__(self, parent, on_goto=None):
        super().__init__(parent)
        self.on_goto = on_goto
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.page = tk.Frame(self.canvas, bg=BG)
        self._window = self.canvas.create_window(0, 0, window=self.page, anchor="nw")
        self.page.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        # The mouse wheel scrolls the page under the pointer.
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

        base = tkfont.nametofont("TkDefaultFont")
        family = base.actual("family")
        self.f_title = tkfont.Font(family=family, size=12, weight="bold")
        self.f_value = tkfont.Font(family=family, size=18, weight="bold")
        self.f_label = tkfont.Font(family=family, size=9)
        self.f_small = tkfont.Font(family=family, size=9)
        self.f_mono = tkfont.nametofont("TkFixedFont").copy()
        self.f_mono.configure(size=10)

    def _bind_wheel(self):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(3, "units"))

    def _unbind_wheel(self):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")

    # --- building blocks ---

    def _clear(self):
        for child in self.page.winfo_children():
            child.destroy()

    def _message(self, text):
        self._clear()
        tk.Label(self.page, text=text, bg=BG, fg=MUTED, font=self.f_title).pack(anchor="w", padx=16, pady=16)

    def _section(self, title, note=""):
        box = tk.Frame(self.page, bg=BG)
        box.pack(fill=tk.X, padx=16, pady=(14, 4))
        tk.Label(box, text=title, bg=BG, fg=TEXT, font=self.f_title).pack(side=tk.LEFT)
        if note:
            tk.Label(box, text="  " + note, bg=BG, fg=MUTED, font=self.f_small).pack(side=tk.LEFT)

    def _card(self, parent=None):
        card = tk.Frame(parent or self.page, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        return card

    def _tile(self, parent, label, value, sub="", color=TEXT):
        card = self._card(parent)
        card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        tk.Label(card, text=label.upper(), bg=CARD, fg=MUTED, font=self.f_label).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(card, text=value, bg=CARD, fg=color, font=self.f_value).pack(anchor="w", padx=12)
        tk.Label(card, text=sub, bg=CARD, fg=MUTED, font=self.f_small, justify="left").pack(
            anchor="w", padx=12, pady=(0, 10))

    def _table(self, columns, rows, height=None, goto_column=None, anchors=None):
        """A Treeview in a card; ``goto_column``: index of the CADU number (double click opens it)."""
        card = self._card()
        card.pack(fill=tk.X, padx=16, pady=(0, 4))
        heading_font = tkfont.nametofont("TkHeadingFont")
        names = [str(k) for k in range(len(columns))] + ["fill"]  # an empty last column takes the spare width
        tree = ttk.Treeview(card, columns=names, show="headings", height=height or min(max(len(rows), 1), 10))
        for k, (heading, width) in enumerate(columns):
            tree.heading(str(k), text=heading)
            anchor = (anchors or {}).get(k, "e")
            tree.column(str(k), width=max(width, heading_font.measure(heading) + 20), anchor=anchor, stretch=False)
        tree.heading("fill", text="")
        tree.column("fill", width=10, stretch=True)
        for row in rows:
            tree.insert("", tk.END, values=tuple(row) + ("",))
        if len(rows) > (height or 10):
            sb = ttk.Scrollbar(card, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.X, padx=1, pady=1)
        if goto_column is not None and self.on_goto:
            def _open(_e):
                item = tree.focus()
                if item:
                    self.on_goto(int(tree.item(item, "values")[goto_column]))
            tree.bind("<Double-1>", _open)
        return tree



class SummaryView(Page):
    """The summary of a file; ``update_view`` fills it, ``on_goto(number)`` opens a CADU."""

    def __init__(self, parent, on_goto=None, check_var=None, on_check=None):
        super().__init__(parent, on_goto)
        self.chart_data = None
        # APIDs whose sequence count continuity is checked (text, e.g. "100, 15-16"); on_check() applies it.
        self.check_var = check_var or tk.StringVar(value=cd.DEFAULT_CHECKED_APIDS)
        self.on_check = on_check
        self._message("Open a file of CADUs to see its summary.")

    def update_view(self, rows, all_rows, data_len, cadu_len, packets=None):
        """Fill the page: ``rows`` analysed, ``all_rows`` every CADU of the file, ``packets`` the packet
        statistics (cadu_decode.packet_statistics)."""
        if not all_rows:
            self._message("No sync marker 1ACFFC1D found.")
            return
        self._clear()
        n = len(all_rows)
        excluded = n - len(rows)
        checked = "rs" in all_rows[0]
        status = Counter(r.get("rs") for r in all_rows)
        bad = [r for r in all_rows if r.get("rs") == "uncorrectable"]

        # Tiles.
        tiles = tk.Frame(self.page, bg=BG)
        tiles.pack(fill=tk.X, padx=16, pady=(16, 0))
        self._tile(tiles, "CADUs", f"{n:,}", f"{cadu_len} bytes each", TEXT)
        if checked:
            fer = len(bad) / n
            self._tile(tiles, "Error-free frames", f"{100 * status['ok'] / n:.2f} %", f"{status['ok']:,} frames\n"
                       "before any correction", TEXT)
            corrected = sum(r["rs_corrected"] for r in all_rows)
            self._tile(tiles, "Corrected frames", f"{status['corrected']:,}",
                       f"{corrected} bytes corrected\n{100 * status['corrected'] / n:.2f} % of the frames",
                       WARN if status["corrected"] else GOOD)
            self._tile(tiles, "Uncorrectable frames", f"{len(bad):,}",
                       f"beyond the Reed-Solomon code\n{100 * fer:.3f} % of the frames", BAD if bad else GOOD)
            self._tile(tiles, "Frame error rate", _fmt_rate(fer), f"uncorrectable / CADUs\n"
                       f"{len(bad)} / {n}", BAD if bad else GOOD)
        else:
            self._tile(tiles, "Reed-Solomon", "not checked", "enable the Reed-Solomon check", MUTED)
        # Header fields read from the good frames only: an uncorrectable frame may have a corrupted header.
        good = [r for r in rows if r.get("rs") != "uncorrectable"]
        mc_jumps, mc_missing = cd._counter_gaps([r["mc_count"] for r in good]) if good else (0, 0)
        scids = Counter(r["scid"] for r in good).most_common()
        self._tile(tiles, "Spacecraft ID", str(scids[0][0]) if scids else "–",
                   (", ".join(f"{k}: {v}" for k, v in scids[1:4]) or "the only one") +
                   f"\nmaster counter: {mc_missing} missing", TEXT)
        if excluded:
            tk.Label(self.page, text=f"Analysis on {len(rows):,} frames: {excluded} uncorrectable frames excluded "
                     "(still counted in the frame error rate).", bg=BG, fg=WARN, font=self.f_small).pack(
                anchor="w", padx=16, pady=(8, 0))

        # Errors along the file.
        if checked:
            cut = max(1, int(n * 0.05))
            core = all_rows[cut:n - cut]
            edges_bad = sum(1 for r in all_rows[:cut] + all_rows[n - cut:] if r["rs"] == "uncorrectable")
            core_bad = sum(1 for r in core if r["rs"] == "uncorrectable")
            self._section("Reed-Solomon errors along the file",
                          "position in the file = time in the pass; shaded: first and last 5 %")
            card = self._card()
            card.pack(fill=tk.X, padx=16)
            self.chart = tk.Canvas(card, height=240, bg=CARD, highlightthickness=0)
            self.chart.pack(fill=tk.X, padx=4, pady=4)
            self._set_chart(all_rows)
            text = (f"First and last 5 % ({cut} + {cut} CADUs): {edges_bad} uncorrectable.   "
                    f"In between: {core_bad} of {len(core)}, frame error rate {_fmt_rate(core_bad / len(core))}."
                    if core else "")
            tk.Label(self.page, text=text, bg=BG, fg=TEXT, font=self.f_small).pack(anchor="w", padx=16, pady=(4, 0))
            tk.Label(self.page, text="Chart: drag or mouse wheel to zoom, right click for the whole file, double "
                     "click to open the CADU.", bg=BG, fg=MUTED, font=self.f_small).pack(anchor="w", padx=16)

        # Virtual channels, from the good frames (the header of an uncorrectable frame is unreliable).
        self._section("Virtual channels", "idle: FHP 7FE; packet start: a packet header in the frame; "
                      "no header: FHP 7FF" + ("; uncorrectable frames not counted" if len(good) < len(rows) else ""))
        if good:
            table = [(row[0], f"{row[1]:,}", *row[2:]) for row in cd.virtual_channel_table(good)[1:]]
            self._table((("VC", 50), ("Frames", 80), ("Share", 70), ("Idle", 60), ("Packet start", 95),
                         ("No header", 80), ("VCFC first", 80), ("VCFC last", 80), ("Jumps", 60),
                         ("Missing", 70), ("RS corrected", 95)), table)
            tk.Label(self.page, text=f"Master channel counter: {good[0]['mc_count']} → {good[-1]['mc_count']}, "
                     f"{mc_jumps} jumps, about {mc_missing} frames missing (8-bit counters: gaps modulo 256)",
                     bg=BG, fg=MUTED, font=self.f_small).pack(anchor="w", padx=16, pady=(4, 0))
        if packets is not None:
            self._packets(packets)

        # CLCW.
        with_ocf = [r for r in rows if r["ocf_flag"]]
        if with_ocf:
            usual = cd.usual_clcw(rows)
            odd = [(r, cd.clcw_differences(r, usual)) for r in rows]
            odd = [(r, d) for r, d in odd if d]
            self._section("CLCW", f"in {len(with_ocf):,} frames")
            card = self._card()
            card.pack(fill=tk.X, padx=16)
            flags = [name for key, name in (("no_rf_available", "No RF available"), ("no_bit_lock", "No bit lock"),
                                            ("lockout", "Lockout"), ("wait", "Wait"), ("retransmit", "Retransmit"))
                     if usual[key]]
            items = (("VC", usual["clcw_vcid"]), ("COP in effect", usual["cop_in_effect"]),
                     ("Status", usual["clcw_status"]), ("Report value", usual["report_value"]),
                     ("FARM-B", usual["farm_b_counter"]), ("Flags set", ", ".join(flags) or "none"),
                     ("Frames", f"{len(with_ocf) - len(odd):,} of {len(with_ocf):,}"))
            tk.Label(card, text="MOST FREQUENT CLCW", bg=CARD, fg=MUTED, font=self.f_label).grid(
                row=0, column=0, columnspan=len(items), sticky="w", padx=12, pady=(8, 2))
            for k, (label, value) in enumerate(items):
                tk.Label(card, text=label, bg=CARD, fg=MUTED, font=self.f_small).grid(row=1, column=k, sticky="w",
                                                                                     padx=12)
                tk.Label(card, text=str(value), bg=CARD, fg=TEXT, font=self.f_title).grid(
                    row=2, column=k, sticky="w", padx=12, pady=(0, 10))
            if odd:
                self._section(f"CADUs with a different CLCW ({len(odd)})",
                              "changes while commanding, or frames with errors; double click: open the CADU")
                self._table((("CADU #", 80), ("Offset", 100), ("VC", 50), ("MCFC", 60), ("RS", 80),
                             ("Different fields (most frequent → this frame)", 400)),
                            [(r["n"], r["offset"], r["vcid"], r["mc_count"], cd.rs_text(r),
                              ", ".join(f"{cd.CLCW_SHORT[f]} {a}→{b}" for f, (a, b) in d.items())) for r, d in odd],
                            goto_column=0, anchors={5: "w"})

        # Uncorrectable frames (not when excluded).
        if bad and not excluded:
            self._section(f"Uncorrectable CADUs ({len(bad)})", "fields as read, may be wrong; double click: open")
            self._table((("CADU #", 80), ("Offset", 100), ("Position in the file", 140), ("VC as read", 90),
                         ("Codewords not correctable", 180)),
                        [(r["n"], r["offset"], f"{100 * (r['n'] - 1) / max(n - 1, 1):.1f} %", r["vcid"],
                          r["rs_failed"]) for r in bad], goto_column=0)
        tk.Frame(self.page, bg=BG, height=16).pack()

    def _packets(self, stats):
        """Packets per VC and per APID, and the sequence count gaps with no frame lost."""
        self._section("Packets per virtual channel", "rebuilt from the frames; idle packets: APID 2047")
        table = []
        for vc in sorted(stats["vcs"]):
            v = stats["vcs"][vc]
            share = v["idle_packet_bytes"] / v["packet_bytes"] if v["packet_bytes"] else 0
            table.append((vc, f"{v['packets']:,}", f"{v['packet_bytes']:,}", f"{v['idle_packet_bytes']:,}",
                          f"{100 * share:.1f} %", v["cut"], v["resyncs"]))
        self._table((("VC", 50), ("Packets", 90), ("Packet bytes", 110), ("Idle packet bytes", 130),
                     ("Idle share", 80), ("Cut at lost frames", 130), ("Resyncs", 70)), table)
        tk.Label(self.page, text="Cut: packets lost where frames of the VC are missing (VC counter jump, "
                 "uncorrectable frame).  Resyncs: packet stream broken with no frame missing.", bg=BG, fg=MUTED,
                 font=self.f_small).pack(anchor="w", padx=16, pady=(4, 0))

        self._section("Packets per APID", "continuity of the sequence count for the APIDs checked only: the "
                      "others may be filtered or sampled on board")
        try:
            checked = cd.parse_apids(self.check_var.get())
        except ValueError:
            checked = set()
        bar = tk.Frame(self.page, bg=BG)
        bar.pack(fill=tk.X, padx=16, pady=(0, 6))
        tk.Label(bar, text="Check the continuity of APIDs:", bg=BG, fg=TEXT, font=self.f_small).pack(side=tk.LEFT)
        entry = ttk.Entry(bar, textvariable=self.check_var, width=18)
        entry.pack(side=tk.LEFT, padx=6)
        if self.on_check:
            entry.bind("<Return>", lambda _e: self.on_check())
            ttk.Button(bar, text="Apply", command=self.on_check).pack(side=tk.LEFT)
        tk.Label(bar, text="e.g. 100 (science data) or 15-16, 100", bg=BG, fg=MUTED,
                 font=self.f_small).pack(side=tk.LEFT, padx=8)
        table, tags = [], []
        for a in stats["apids"]:
            idle = a["apid"] == cd.IDLE_APID
            check = a["apid"] in checked and not idle
            length = str(a["min_length"]) if a["min_length"] == a["max_length"] else \
                f"{a['min_length']}–{a['max_length']}"
            table.append((f"{a['apid']}" + (" (idle)" if idle else ""), a["vc"], f"{a['packets']:,}",
                          f"{a['bytes']:,}", length, a["first_seq"], a["last_seq"],
                          ("complete" if not (a["missing"] or a["missing_lost_frames"]) else "gaps") if check else "",
                          a["missing"] if check else "", a["missing_lost_frames"] if check else "",
                          a["back_jumps"] if check else "", a["repeated"] if check else ""))
            tags.append(("" if not check else "gap" if a["missing"] or a["missing_lost_frames"] else "ok"))
        tree = self._table((("APID", 90), ("VC", 50), ("Packets", 90), ("Bytes", 110), ("Length", 80),
                            ("Seq first", 75), ("Seq last", 75), ("Continuity", 80),
                            ("Missing, no frame lost", 150), ("Missing, lost frames", 140), ("Back jumps", 85),
                            ("Repeated", 80)), table)
        for item, tag in zip(tree.get_children(), tags):
            if tag:
                tree.item(item, tags=(tag,))
        tree.tag_configure("gap", foreground=WARN)
        tree.tag_configure("ok", foreground=GOOD)

        gaps = [g for g in stats["gaps"] if g["apid"] in checked]
        if gaps:
            self._section(f"Sequence count gaps with no frame lost, APIDs checked ({len(gaps)})",
                          "packets that never reached this VC; double click: open the CADU after the gap")
            self._table((("APID", 60), ("VC", 50), ("Count before", 100), ("Count after", 100), ("Missing", 70),
                         ("CADU before", 100), ("CADU after", 100), ("Missing counts found in", 180)),
                        [(g["apid"], g["vc"], g["before"], g["after"], g["missing"], g["frame_before"], g["frame"],
                          ", ".join(f"VC {v}" for v in g["found_in"]) or "–") for g in gaps],
                        goto_column=6, anchors={7: "w"})

    # --- chart of the errors along the file ---

    # Plot area inside the chart canvas.
    MARGINS = (60, 40, 30, 40)  # left, right, top, bottom

    def _set_chart(self, rows):
        """Data of the chart, with running sums so that any part of the file is summed at once."""
        self.chart_data = rows
        self.chart_view = (0, len(rows))
        corr, failed, fixed = [0], [0], [0]
        for r in rows:
            corr.append(corr[-1] + r["rs_corrected"])
            failed.append(failed[-1] + (r["rs"] == "uncorrectable"))
            fixed.append(fixed[-1] + (r["rs"] == "corrected"))
        self._sum_corr, self._sum_failed, self._sum_fixed = corr, failed, fixed
        self._bad_index = [k for k, r in enumerate(rows) if r["rs"] == "uncorrectable"]
        self._drag = None
        c = self.chart
        c.bind("<Configure>", lambda _e: self._draw_chart())
        c.bind("<Motion>", self._chart_motion)
        c.bind("<Leave>", lambda _e: c.delete("tip"))
        c.bind("<ButtonPress-1>", self._chart_press)
        c.bind("<B1-Motion>", self._chart_drag)
        c.bind("<ButtonRelease-1>", self._chart_release)
        c.bind("<Double-Button-1>", self._chart_open)
        c.bind("<Button-3>", lambda _e: self._zoom(0, len(rows)))
        c.bind("<MouseWheel>", lambda e: self._chart_wheel(e, e.delta > 0))
        c.bind("<Button-4>", lambda e: self._chart_wheel(e, True))
        c.bind("<Button-5>", lambda e: self._chart_wheel(e, False))

    def _plot_box(self):
        left, right, top, bottom = self.MARGINS
        return left, top, self.chart.winfo_width() - left - right, self.chart.winfo_height() - top - bottom

    def _index_at(self, x: float) -> int:
        """Index (in the rows) of the CADU under the abscissa ``x``."""
        left, _top, pw, _ph = self._plot_box()
        lo, hi = self.chart_view
        k = lo + int((x - left) / pw * (hi - lo))
        return min(max(k, lo), hi - 1)

    def _x_of(self, index: float) -> float:
        left, _top, pw, _ph = self._plot_box()
        lo, hi = self.chart_view
        return left + (index - lo) / (hi - lo) * pw

    def _bins(self):
        """(start, end) index of each bar of the view: bars about 5 pixels wide, one per CADU when zoomed in."""
        lo, hi = self.chart_view
        m = hi - lo
        bins = min(m, max(80, self._plot_box()[2] // 5))
        return [(lo + k * m // bins, lo + (k + 1) * m // bins) for k in range(bins)]

    def _zoom(self, lo: int, hi: int) -> None:
        n = len(self.chart_data)
        lo, hi = max(0, lo), min(n, hi)
        if hi - lo < 10:  # at least 10 CADUs on screen
            mid = (lo + hi) // 2
            lo, hi = max(0, mid - 5), min(n, max(0, mid - 5) + 10)
        self.chart_view = (lo, hi)
        self._draw_chart()

    def _chart_wheel(self, event, zoom_in: bool):
        """Mouse wheel on the chart: zoom in or out around the pointer."""
        lo, hi = self.chart_view
        k = self._index_at(event.x)
        factor = 0.5 if zoom_in else 2.0
        share = (k - lo) / max(hi - lo, 1)
        width = max(10, int((hi - lo) * factor))
        start = int(k - share * width)
        n = len(self.chart_data)
        start = min(max(0, start), max(0, n - width))
        self._zoom(start, start + width)
        self._chart_motion(event)
        return "break"  # do not scroll the page

    def _chart_press(self, event):
        left, top, pw, ph = self._plot_box()
        if left <= event.x <= left + pw:
            self._drag = event.x

    def _chart_drag(self, event):
        if self._drag is None:
            return
        left, top, pw, ph = self._plot_box()
        x = min(max(event.x, left), left + pw)
        c = self.chart
        c.delete("select", "tip")
        c.create_rectangle(min(x, self._drag), top, max(x, self._drag), top + ph, fill=ACCENT,
                           stipple="gray25", outline=ACCENT, tags="select")

    def _chart_release(self, event):
        start, self._drag = self._drag, None
        self.chart.delete("select")
        if start is None or abs(event.x - start) < 4:
            return
        a, b = sorted((start, event.x))
        self._zoom(self._index_at(a), self._index_at(b) + 1)

    def _chart_open(self, event):
        """Double click: open the uncorrectable frame under the pointer, otherwise the CADU there."""
        if not self.on_goto:
            return
        near = self._bad_near(event.x)
        k = near[0] if near else self._index_at(event.x)
        self.on_goto(self.chart_data[k]["n"])

    def _bad_near(self, x: float, pixels: float = 3) -> list[int]:
        """Indexes of the uncorrectable frames drawn within ``pixels`` of ``x``."""
        from bisect import bisect_left, bisect_right
        lo, hi = self.chart_view
        a, b = self._index_at(x - pixels), self._index_at(x + pixels)
        found = self._bad_index[bisect_left(self._bad_index, max(a, lo)):bisect_right(self._bad_index, min(b, hi - 1))]
        return sorted(found, key=lambda k: abs(self._x_of(k + 0.5) - x))

    def _chart_motion(self, event):
        """Tooltip: the CADU under the pointer, its bar and the uncorrectable frames nearby."""
        c = self.chart
        c.delete("tip")
        if self._drag is not None or not self.chart_data:
            return
        left, top, pw, ph = self._plot_box()
        if not (left <= event.x <= left + pw and top <= event.y <= top + ph):
            return
        rows, n = self.chart_data, len(self.chart_data)
        k = self._index_at(event.x)
        a, b = next((a, b) for a, b in self._bins() if a <= k < b)
        lines = [f"CADU #{rows[k]['n']}  ({100 * k / max(n - 1, 1):.2f} % of the file)  RS {cd.rs_text(rows[k])}"]
        if b - a > 1:
            lines.append(f"Bar: CADUs #{rows[a]['n']}-#{rows[b - 1]['n']} ({b - a})")
        corr = self._sum_corr[b] - self._sum_corr[a]
        lines.append(f"  {corr / (b - a):.2f} bytes corrected per frame, {self._sum_fixed[b] - self._sum_fixed[a]} "
                     f"corrected, {self._sum_failed[b] - self._sum_failed[a]} uncorrectable")
        near = self._bad_near(event.x)
        if near:
            names = ", ".join(f"#{rows[j]['n']}" for j in sorted(near)[:6]) + (" …" if len(near) > 6 else "")
            lines.append(f"Uncorrectable here: {len(near)} ({names}); double click opens #{rows[near[0]]['n']}")
        c.create_line(event.x, top, event.x, top + ph, fill=MUTED, dash=(2, 2), tags="tip")
        text = c.create_text(0, 0, text="\n".join(lines), anchor="nw", fill=TEXT, font=self.f_small, tags="tip")
        x0, y0, x1, y1 = c.bbox(text)
        tw, th = x1 - x0 + 12, y1 - y0 + 10
        x = event.x + 14 if event.x + 14 + tw < c.winfo_width() else event.x - 14 - tw
        y = min(max(event.y - th - 8, 2), c.winfo_height() - th - 2)
        c.coords(text, x + 6, y + 5)
        box = c.create_rectangle(x, y, x + tw, y + th, fill="#fffbe6", outline=BORDER, tags="tip")
        c.tag_raise(text, box)

    def _draw_chart(self):
        """Bars: bytes corrected per frame in each part of the view; red ticks: uncorrectable frames."""
        c, rows = self.chart, self.chart_data
        c.delete("all")
        if c.winfo_width() < 50 or not rows:
            return
        left, top, pw, ph = self._plot_box()
        n = len(rows)
        lo, hi = self.chart_view
        bins = self._bins()
        values = [(self._sum_corr[b] - self._sum_corr[a]) / max(b - a, 1) for a, b in bins]
        top_value = max(values) or 1.0
        # Edges (first and last 5 % of the file), where they are in view.
        cut = max(1, int(n * 0.05))
        for a, b in ((0, cut), (n - cut, n)):
            a, b = max(a, lo), min(b, hi)
            if a < b:
                c.create_rectangle(self._x_of(a), top, self._x_of(b), top + ph, fill=EDGE, outline="")
        # Grid and y axis.
        for k in range(5):
            y = top + ph - k * ph / 4
            c.create_line(left, y, left + pw, y, fill="#e3e8ef")
            c.create_text(left - 8, y, text=f"{top_value * k / 4:.3g}", anchor="e", fill=MUTED, font=self.f_small)
        # Bars.
        for (a, b), v in zip(bins, values):
            if v > 0:
                x0, x1 = self._x_of(a), self._x_of(b)
                pad = (x1 - x0) * 0.15
                c.create_rectangle(x0 + pad, top + ph - v / top_value * ph, max(x1 - pad, x0 + pad + 1), top + ph,
                                   fill=ACCENT, outline="")
        # Uncorrectable frames.
        from bisect import bisect_left
        for k in self._bad_index[bisect_left(self._bad_index, lo):bisect_left(self._bad_index, hi)]:
            x = self._x_of(k + 0.5)
            c.create_line(x, top, x, top + ph, fill=BAD, width=1.5)
        # X axis.
        c.create_line(left, top + ph, left + pw, top + ph, fill=MUTED)
        for k in range(5):
            x = left + k * pw / 4
            index = min(lo + round(k * (hi - lo - 1) / 4), n - 1)
            c.create_text(x, top + ph + 12, text=f"{100 * index / max(n - 1, 1):.4g} %", fill=MUTED,
                          font=self.f_small)
            c.create_text(x, top + ph + 26, text=f"#{rows[index]['n']}", fill=MUTED, font=self.f_small)
        # Legend and zoom state.
        lx = left + pw - 330
        c.create_rectangle(lx, 6, lx + 12, 18, fill=ACCENT, outline="")
        c.create_text(lx + 18, 12, text="bytes corrected per frame", anchor="w", fill=TEXT, font=self.f_small)
        c.create_line(lx + 190, 6, lx + 190, 18, fill=BAD, width=2)
        c.create_text(lx + 198, 12, text="uncorrectable frame", anchor="w", fill=TEXT, font=self.f_small)
        per_bar = (hi - lo) / len(bins)
        state = (f"CADUs #{rows[lo]['n']}-#{rows[hi - 1]['n']} of {n}; " if (lo, hi) != (0, n) else "Whole file; ") + \
            ("one bar per CADU" if per_bar == 1 else f"one bar per {per_bar:.0f} CADUs")
        c.create_text(left, 12, text=state, anchor="w", fill=MUTED, font=self.f_small)
