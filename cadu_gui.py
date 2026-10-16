#!/usr/bin/env python3
"""Window for cadu_decode.py: open a file of CADUs, see the summary, browse the frames, show one CADU.

    python cadu_gui.py [capture.bin]

Tkinter only (part of Python). The decoding is the one of cadu_decode.py.
"""

import csv
import os
import sys
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

import cadu_decode as cd

COLUMNS = (  # (key, heading, width)
    ("n", "#", 70), ("offset", "Offset", 90), ("scid", "SCID", 55), ("vcid", "VC", 40), ("mc_count", "MCFC", 55),
    ("vc_count", "VCFC", 55), ("fhp", "FHP", 150), ("segment_length_id", "Seg", 40), ("ocf_flag", "OCF", 40),
    ("clcw_vcid", "CLCW VC", 80), ("cop_in_effect", "COP", 45), ("flags", "CLCW flags", 140),
    ("report_value", "Report", 60), ("rs", "RS", 75),
)
ALL = "All"


class CaduWindow:
    def __init__(self, root: tk.Tk, path=None):
        self.root = root
        self.data = b""
        self.rows: list[dict] = []
        self.path = None
        self.cadu_len = 0
        self.randomized = True
        self.usual = None
        root.title("CADU Decoder")
        root.geometry("1250x820")
        self.mono = tkfont.nametofont("TkFixedFont").copy()
        self.mono.configure(size=10)

        bar = ttk.Frame(root, padding=(10, 8))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Open…", command=self.open_file).pack(side=tk.LEFT)
        self.interleave_var = tk.IntVar(value=5)
        self.randomized_var = tk.StringVar(value="Auto")
        self.fecf_var = tk.BooleanVar(value=False)
        ttk.Label(bar, text="  RS interleaving I").pack(side=tk.LEFT)
        ttk.Spinbox(bar, from_=1, to=8, width=3, textvariable=self.interleave_var,
                    command=self.reload).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(bar, text="Randomized").pack(side=tk.LEFT)
        box = ttk.Combobox(bar, textvariable=self.randomized_var, values=("Auto", "Yes", "No"), width=6,
                           state="readonly")
        box.pack(side=tk.LEFT, padx=(4, 0))
        box.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        ttk.Checkbutton(bar, text="FECF", variable=self.fecf_var, command=self.reload).pack(side=tk.LEFT, padx=(8, 0))
        self.rs_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Reed-Solomon check", variable=self.rs_var,
                        command=self.reload).pack(side=tk.LEFT, padx=(12, 0))
        self.exclude_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Exclude uncorrectable frames", variable=self.exclude_var,
                        command=self.refresh).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="Export CSV…", command=self.export_csv).pack(side=tk.RIGHT)
        self.file_var = tk.StringVar(value="No file: press Open… (CADUs starting with the sync marker 1ACFFC1D)")
        ttk.Label(root, textvariable=self.file_var, padding=(10, 0)).pack(fill=tk.X)

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.summary_text = self._text(self.tabs)
        self.tabs.add(self.summary_text.master, text="Summary")

        frames_tab = ttk.Frame(self.tabs)
        self.tabs.add(frames_tab, text="Frames")
        filters = ttk.Frame(frames_tab, padding=(0, 4))
        filters.pack(fill=tk.X)
        ttk.Label(filters, text="Virtual channel").pack(side=tk.LEFT)
        self.vc_var = tk.StringVar(value=ALL)
        self.vc_box = ttk.Combobox(filters, textvariable=self.vc_var, values=[ALL], width=8, state="readonly")
        self.vc_box.pack(side=tk.LEFT, padx=(4, 12))
        self.vc_box.bind("<<ComboboxSelected>>", lambda _e: self.fill_frames())
        self.idle_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(filters, text="Idle frames", variable=self.idle_var,
                        command=self.fill_frames).pack(side=tk.LEFT)
        self.rserr_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="Only RS errors", variable=self.rserr_var,
                        command=self.fill_frames).pack(side=tk.LEFT, padx=(12, 0))
        self.odd_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="Only unusual CLCW", variable=self.odd_var,
                        command=self.fill_frames).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(filters, text="   Go to #").pack(side=tk.LEFT)
        self.goto_var = tk.StringVar()
        goto = ttk.Entry(filters, textvariable=self.goto_var, width=8)
        goto.pack(side=tk.LEFT, padx=4)
        goto.bind("<Return>", lambda _e: self.goto())
        ttk.Button(filters, text="Show", command=self.goto).pack(side=tk.LEFT)
        self.dump_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(filters, text="Hex dump", variable=self.dump_var,
                        command=self.show_selected).pack(side=tk.LEFT, padx=(12, 0))
        self.count_var = tk.StringVar()
        ttk.Label(filters, textvariable=self.count_var).pack(side=tk.RIGHT)

        panes = ttk.PanedWindow(frames_tab, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True)
        table_box = ttk.Frame(panes)
        self.tree = ttk.Treeview(table_box, columns=[c[0] for c in COLUMNS], show="headings", height=12)
        for key, heading, width in COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor="e" if key not in ("fhp", "flags") else "w", stretch=False)
        scroll = ttk.Scrollbar(table_box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_selected())
        panes.add(table_box, weight=1)
        self.detail_text = self._text(panes)
        panes.add(self.detail_text.master, weight=2)

        if path:
            self.load(path)

    def _text(self, parent) -> tk.Text:
        box = ttk.Frame(parent)
        text = tk.Text(box, wrap="none", font=self.mono, padx=8, pady=6, relief="flat")
        ys = ttk.Scrollbar(box, orient=tk.VERTICAL, command=text.yview)
        xs = ttk.Scrollbar(box, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set, state="disabled")
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        xs.pack(side=tk.BOTTOM, fill=tk.X)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        return text

    @staticmethod
    def _set_text(widget: tk.Text, lines) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", "\n".join(lines))
        widget.configure(state="disabled")

    # --- file ---

    def open_file(self) -> None:
        path = filedialog.askopenfilename(title="Open a file of CADUs",
                                          filetypes=[("All files", "*.*"), ("Binary", "*.bin *.dat *.raw")])
        if path:
            self.load(path)

    def load(self, path: str) -> None:
        try:
            self.data = cd.read_input(path)
        except OSError as exc:
            messagebox.showerror("Open", f"Cannot read {path}: {exc}")
            return
        self.path = path
        self.reload()

    def reload(self) -> None:
        if not self.path:
            return
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            interleave = int(self.interleave_var.get())
            choice = self.randomized_var.get()
            if choice == "Auto":
                self.randomized = cd.detect_randomization(self.data, interleave, self.fecf_var.get())
                how = ("randomized" if self.randomized else "not randomized, already de-randomized") + " (detected)"
            else:
                self.randomized = choice == "Yes"
                how = "randomized" if self.randomized else "not randomized"
            name = os.path.basename(self.path)

            def progress(done, total):
                self.file_var.set(f"{name}: Reed-Solomon check, {done} of {total} CADUs...")
                self.root.update_idletasks()

            self.rows = cd.decode_all(self.data, interleave, self.randomized, self.fecf_var.get(),
                                      reed_solomon=self.rs_var.get(), progress=progress)
        finally:
            self.root.config(cursor="")
        self.cadu_len = len(cd.ASM) + cd.RS_N * interleave
        self.file_var.set(f"{os.path.basename(self.path)}: {len(self.data)} bytes; CADU {self.cadu_len} bytes, "
                          f"transfer frame {cd.RS_K * interleave} bytes (I = {interleave}); codeblocks {how}")
        self.refresh()

    def _analysed(self):
        if self.exclude_var.get():
            return [r for r in self.rows if r.get("rs") != "uncorrectable"]
        return self.rows

    def refresh(self) -> None:
        """Summary and frame table of the frames analysed (without the uncorrectable ones when excluded)."""
        if self.rows:
            self._set_text(self.summary_text, cd.summary(self._analysed(), len(self.data), self.cadu_len,
                                                         all_rows=self.rows))
        else:
            self._set_text(self.summary_text, ["No sync marker 1ACFFC1D found."])
        self.usual = cd.usual_clcw(self._analysed())
        vcs = sorted({r["vcid"] for r in self.rows})
        self.vc_box.configure(values=[ALL] + [str(v) for v in vcs])
        if self.vc_var.get() not in self.vc_box.cget("values"):
            self.vc_var.set(ALL)
        self.fill_frames()
        self._set_text(self.detail_text, ["Select a frame to see its content."])

    # --- frames ---

    def _visible_rows(self):
        vc = self.vc_var.get()
        for r in self._analysed():
            if vc != ALL and r["vcid"] != int(vc):
                continue
            if self.rserr_var.get() and r.get("rs", "ok") == "ok":
                continue
            if not self.idle_var.get() and r["first_header_pointer"] == 0x7FE:
                continue
            if self.odd_var.get() and not cd.clcw_differences(r, self.usual):
                continue
            yield r

    def fill_frames(self) -> None:
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for r in self._visible_rows():
            if r["ocf_flag"]:
                flags = ",".join(name for key, name in (
                    ("no_rf_available", "NoRF"), ("no_bit_lock", "NoLock"), ("lockout", "Lockout"),
                    ("wait", "Wait"), ("retransmit", "Retx")) if r[key]) or "–"
                clcw = (r["clcw_vcid"], r["cop_in_effect"], flags, r["report_value"])
            else:
                clcw = ("–", "–", "–", "–")
            self.tree.insert("", tk.END, iid=str(r["n"]), values=(
                r["n"], r["offset"], r["scid"], r["vcid"], r["mc_count"], r["vc_count"],
                cd.first_header_pointer_text(r["first_header_pointer"]), r["segment_length_id"], r["ocf_flag"],
                *clcw, cd.rs_text(r)))
            shown += 1
        self.count_var.set(f"{shown} of {len(self.rows)} frames")

    def goto(self) -> None:
        text = self.goto_var.get().strip()
        try:
            number = cd.parse_selection(text, len(self.rows))[0]
        except (ValueError, IndexError):
            messagebox.showerror("Go to", f"Enter a CADU number from 1 to {len(self.rows)}.")
            return
        if not self.tree.exists(str(number)):  # hidden by the filters
            self.vc_var.set(ALL)
            self.idle_var.set(True)
            self.odd_var.set(False)
            self.rserr_var.set(False)
            if self.exclude_var.get() and self.rows[number - 1].get("rs") == "uncorrectable":
                self.exclude_var.set(False)
                self.refresh()
            self.fill_frames()
        self.tree.selection_set(str(number))
        self.tree.see(str(number))

    def show_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        number = int(selection[0])
        lines = cd.show_cadu(number, self.rows[number - 1], self.data, int(self.interleave_var.get()),
                             self.randomized, self.fecf_var.get(), dump=self.dump_var.get(),
                             reed_solomon=self.rs_var.get())
        self._set_text(self.detail_text, lines)

    def export_csv(self) -> None:
        if not self.rows:
            messagebox.showinfo("Export CSV", "Open a file of CADUs first.")
            return
        path = filedialog.asksaveasfilename(title="Export the frames", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = self.rows
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            writer.writeheader()
            writer.writerows(rows)
        messagebox.showinfo("Export CSV", f"{len(rows)} frames written to {path}")


def main() -> None:
    root = tk.Tk()
    CaduWindow(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
