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

    def __init__(self, parent, on_goto=None):
        super().__init__(parent, on_goto)
        self.chart_data = None
        self._message("Open a file of CADUs to see its summary.")

    def update_view(self, rows, all_rows, data_len, cadu_len):
        """Fill the page: ``rows`` analysed, ``all_rows`` every CADU of the file."""
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
            self._tile(tiles, "Frame error rate", _fmt_rate(fer), f"{len(bad)} uncorrectable of {n}\n"
                       f"{100 * fer:.3f} %", BAD if bad else GOOD)
            corrected = sum(r["rs_corrected"] for r in all_rows)
            self._tile(tiles, "Corrected frames", f"{status['corrected']:,}",
                       f"{corrected} bytes corrected\n{100 * status['corrected'] / n:.2f} % of the frames",
                       WARN if status["corrected"] else GOOD)
            self._tile(tiles, "Error-free frames", f"{100 * status['ok'] / n:.2f} %", f"{status['ok']:,} frames\n"
                       "before any correction", TEXT)
        else:
            self._tile(tiles, "Reed-Solomon", "not checked", "enable the Reed-Solomon check", MUTED)
        mc_jumps, mc_missing = cd._counter_gaps([r["mc_count"] for r in rows]) if rows else (0, 0)
        scids = Counter(r["scid"] for r in rows).most_common()
        self._tile(tiles, "Spacecraft ID", str(scids[0][0]) if scids else "–",
                   (", ".join(f"{k}: {v}" for k, v in scids[1:4]) or "the only one") +
                   f"\nmaster counter: {mc_missing} missing", WARN if len(scids) > 1 else TEXT)
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
            self.chart_data = all_rows
            self.chart.bind("<Configure>", lambda _e: self._draw_chart())
            text = (f"First and last 5 % ({cut} + {cut} CADUs): {edges_bad} uncorrectable.   "
                    f"In between: {core_bad} of {len(core)}, frame error rate {_fmt_rate(core_bad / len(core))}."
                    if core else "")
            tk.Label(self.page, text=text, bg=BG, fg=TEXT, font=self.f_small).pack(anchor="w", padx=16, pady=(4, 0))

        # Virtual channels.
        self._section("Virtual channels", "idle: FHP 7FE; packet start: a packet header in the frame; "
                      "no header: FHP 7FF")
        table = []
        for vc in sorted({r["vcid"] for r in rows}):
            frames = [r for r in rows if r["vcid"] == vc]
            jumps, missing = cd._counter_gaps([r["vc_count"] for r in frames])
            fhp = Counter(r["first_header_pointer"] for r in frames)
            table.append((vc, f"{len(frames):,}", f"{100 * len(frames) / len(rows):.1f} %", fhp[0x7FE],
                          len(frames) - fhp[0x7FE] - fhp[0x7FF], fhp[0x7FF], frames[0]["vc_count"],
                          frames[-1]["vc_count"], jumps, missing,
                          sum(1 for r in frames if r.get("rs") == "corrected") if checked else "–",
                          sum(1 for r in frames if r.get("rs") == "uncorrectable") if checked else "–"))
        self._table((("VC", 50), ("Frames", 80), ("Share", 70), ("Idle", 60), ("Packet start", 95),
                     ("No header", 80), ("VCFC first", 80), ("VCFC last", 80), ("Jumps", 60), ("Missing", 70),
                     ("RS corrected", 95), ("Uncorrectable", 100)), table)
        tk.Label(self.page, text=f"Master channel counter: {rows[0]['mc_count'] if rows else '–'} → "
                 f"{rows[-1]['mc_count'] if rows else '–'}, {mc_jumps} jumps, about {mc_missing} frames missing "
                 "(8-bit counters: gaps modulo 256)", bg=BG, fg=MUTED, font=self.f_small).pack(
            anchor="w", padx=16, pady=(4, 0))

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

    def _draw_chart(self):
        """Bars: bytes corrected per frame in each part of the file; red ticks: uncorrectable frames."""
        c, rows = self.chart, self.chart_data
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or not rows:
            return
        left, right, top, bottom = 60, 40, 30, 40
        pw, ph = w - left - right, h - top - bottom
        n = len(rows)
        bins = max(1, min(80, n))
        values, fails = [], []
        for k in range(bins):
            part = rows[k * n // bins:(k + 1) * n // bins]
            values.append(sum(r["rs_corrected"] for r in part) / max(len(part), 1))
        top_value = max(values) or 1.0
        # Edges (first and last 5 %).
        edge = max(1, int(n * 0.05)) / n * pw
        c.create_rectangle(left, top, left + edge, top + ph, fill=EDGE, outline="")
        c.create_rectangle(left + pw - edge, top, left + pw, top + ph, fill=EDGE, outline="")
        # Grid and y axis.
        for k in range(5):
            y = top + ph - k * ph / 4
            c.create_line(left, y, left + pw, y, fill="#e3e8ef")
            c.create_text(left - 8, y, text=f"{top_value * k / 4:.1f}", anchor="e", fill=MUTED, font=self.f_small)
        # Bars.
        bw = pw / bins
        for k, v in enumerate(values):
            if v > 0:
                x0 = left + k * bw + bw * 0.15
                c.create_rectangle(x0, top + ph - v / top_value * ph, x0 + bw * 0.7, top + ph, fill=ACCENT,
                                   outline="")
        # Uncorrectable frames.
        for r in rows:
            if r["rs"] == "uncorrectable":
                x = left + (r["n"] - 0.5) / n * pw
                c.create_line(x, top, x, top + ph, fill=BAD, width=1.5)
        # X axis.
        c.create_line(left, top + ph, left + pw, top + ph, fill=MUTED)
        for k in range(5):
            x = left + k * pw / 4
            number = 1 + round(k * (n - 1) / 4)
            c.create_text(x, top + ph + 12, text=f"{25 * k} %", fill=MUTED, font=self.f_small)
            c.create_text(x, top + ph + 26, text=f"#{number}", fill=MUTED, font=self.f_small)
        # Legend.
        lx = left + pw - 330
        c.create_rectangle(lx, 6, lx + 12, 18, fill=ACCENT, outline="")
        c.create_text(lx + 18, 12, text="bytes corrected per frame", anchor="w", fill=TEXT, font=self.f_small)
        c.create_line(lx + 190, 6, lx + 190, 18, fill=BAD, width=2)
        c.create_text(lx + 198, 12, text="uncorrectable frame", anchor="w", fill=TEXT, font=self.f_small)
